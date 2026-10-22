package io.linkdesk.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.util.Base64;
import android.util.DisplayMetrics;
import android.view.WindowManager;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;

/** Captures the screen while a session is live (Android requires a visible notification). */
public class CaptureService extends Service {
    static final String CHANNEL = "linkdesk_session";
    static volatile CaptureService instance;

    MediaProjection projection;
    VirtualDisplay display;
    ImageReader reader;
    HandlerThread thread;
    int capW, capH;
    private Bitmap latest;
    private long seq, sentSeq;

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) { stopSelf(); return START_NOT_STICKY; }
        Notification n = buildNotification();
        if (Build.VERSION.SDK_INT >= 29) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
        else startForeground(1, n);

        try {
            start(intent.getIntExtra("code", 0), (Intent) intent.getParcelableExtra("data"));
            instance = this;
            MainActivity a = MainActivity.current;
            if (a != null) a.onCaptureReady(true);
        } catch (Exception e) {
            MainActivity a = MainActivity.current;
            if (a != null) a.onCaptureReady(false);
            stopSelf();
        }
        return START_NOT_STICKY;
    }

    private void start(int code, Intent data) {
        thread = new HandlerThread("capture");
        thread.start();
        Handler handler = new Handler(thread.getLooper());

        MediaProjectionManager mpm = (MediaProjectionManager) getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        projection = mpm.getMediaProjection(code, data);
        projection.registerCallback(new MediaProjection.Callback() {
            @Override
            public void onStop() {
                MainActivity a = MainActivity.current;
                if (a != null) a.js("window.LD && LD.onCaptureStopped && LD.onCaptureStopped()");
                stopSelf();
            }
        }, handler);

        DisplayMetrics dm = new DisplayMetrics();
        ((WindowManager) getSystemService(WINDOW_SERVICE)).getDefaultDisplay().getRealMetrics(dm);
        float scale = Math.min(1f, 720f / Math.min(dm.widthPixels, dm.heightPixels));
        capW = Math.round(dm.widthPixels * scale) & ~1;
        capH = Math.round(dm.heightPixels * scale) & ~1;

        reader = ImageReader.newInstance(capW, capH, PixelFormat.RGBA_8888, 2);
        reader.setOnImageAvailableListener(r -> {
            Image img = null;
            try {
                img = r.acquireLatestImage();
                if (img == null) return;
                Image.Plane p = img.getPlanes()[0];
                ByteBuffer buf = p.getBuffer();
                int rowPixels = p.getRowStride() / p.getPixelStride();
                Bitmap bmp = Bitmap.createBitmap(rowPixels, capH, Bitmap.Config.ARGB_8888);
                bmp.copyPixelsFromBuffer(buf);
                if (rowPixels != capW) {
                    Bitmap cropped = Bitmap.createBitmap(bmp, 0, 0, capW, capH);
                    bmp.recycle();
                    bmp = cropped;
                }
                synchronized (this) {
                    latest = bmp;
                    seq++;
                }
            } catch (Exception ignored) {
            } finally {
                if (img != null) img.close();
            }
        }, handler);

        display = projection.createVirtualDisplay("LinkDesk", capW, capH, Math.max(120, (int) (dm.densityDpi * scale)),
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, reader.getSurface(), null, handler);
    }

    /** Latest screen as base64 JPEG, or "" if nothing changed since the last call. */
    String frameBase64() {
        Bitmap bmp;
        synchronized (this) {
            if (latest == null || seq == sentSeq) return "";
            bmp = latest;
            sentSeq = seq;
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream(120_000);
        bmp.compress(Bitmap.CompressFormat.JPEG, 60, out);
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP);
    }

    private Notification buildNotification() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Remote session", NotificationManager.IMPORTANCE_LOW));
            b = new Notification.Builder(this, CHANNEL);
        } else {
            b = new Notification.Builder(this);
        }
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return b.setContentTitle("LinkDesk: your screen is being shared")
                .setContentText("Tap to open LinkDesk and disconnect")
                .setSmallIcon(R.drawable.ic_stat)
                .setContentIntent(open)
                .setOngoing(true)
                .build();
    }

    @Override
    public void onDestroy() {
        instance = null;
        try { if (display != null) display.release(); } catch (Exception ignored) { }
        try { if (projection != null) projection.stop(); } catch (Exception ignored) { }
        try { if (reader != null) reader.close(); } catch (Exception ignored) { }
        if (thread != null) thread.quitSafely();
        super.onDestroy();
    }
}
