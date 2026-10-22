package io.linkdesk.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.provider.MediaStore;
import android.provider.Settings;
import android.util.Base64;
import android.webkit.JavascriptInterface;

import org.json.JSONArray;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.util.HashMap;
import java.util.Map;

/** Methods the page can call as window.Android.* (they run on a background thread). */
public class Bridge {
    private final MainActivity act;
    private final SharedPreferences prefs;
    private final Map<Integer, OutputStream> files = new HashMap<>();
    private final Map<Integer, String> fileNames = new HashMap<>();
    private int nextFile = 1;

    Bridge(MainActivity act) {
        this.act = act;
        this.prefs = act.getSharedPreferences("linkdesk", Context.MODE_PRIVATE);
    }

    // ---- settings
    @JavascriptInterface public String getPref(String key) { return prefs.getString(key, ""); }
    @JavascriptInterface public void setPref(String key, String value) { prefs.edit().putString(key, value).apply(); }
    @JavascriptInterface public String deviceName() { return Build.MANUFACTURER + " " + Build.MODEL; }

    @JavascriptInterface public void copyText(String text) {
        act.runOnUiThread(() -> {
            ClipboardManager cm = (ClipboardManager) act.getSystemService(Context.CLIPBOARD_SERVICE);
            cm.setPrimaryClip(ClipData.newPlainText("LinkDesk ID", text));
        });
    }

    // ---- screen
    @JavascriptInterface public void requestCapture() { act.runOnUiThread(act::requestCapture); }
    @JavascriptInterface public void stopCapture() { act.stopService(new Intent(act, CaptureService.class)); }

    @JavascriptInterface public String getFrame() {
        CaptureService s = CaptureService.instance;
        return s == null ? "" : s.frameBase64();
    }

    @JavascriptInterface public String frameSize() {
        CaptureService s = CaptureService.instance;
        return s == null ? "0,0" : s.capW + "," + s.capH;
    }

    // ---- control
    @JavascriptInterface public boolean controlEnabled() { return ControlService.instance != null; }

    @JavascriptInterface public void openAccessibility() {
        act.runOnUiThread(() -> act.startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
    }

    @JavascriptInterface public void tap(double x, double y) {
        ControlService c = ControlService.instance;
        if (c != null) c.tap(x, y);
    }

    @JavascriptInterface public void longPress(double x, double y) {
        ControlService c = ControlService.instance;
        if (c != null) c.longPress(x, y);
    }

    @JavascriptInterface public void swipe(String json, int durationMs) {
        ControlService c = ControlService.instance;
        if (c == null) return;
        try {
            JSONArray arr = new JSONArray(json);
            double[][] pts = new double[arr.length()][2];
            for (int i = 0; i < arr.length(); i++) {
                JSONArray p = arr.getJSONArray(i);
                pts[i][0] = p.getDouble(0);
                pts[i][1] = p.getDouble(1);
            }
            c.swipe(pts, durationMs);
        } catch (Exception ignored) { }
    }

    @JavascriptInterface public void scroll(double x, double y, double dy) {
        ControlService c = ControlService.instance;
        if (c != null) c.scroll(x, y, dy);
    }

    @JavascriptInterface public void nav(String action) {
        ControlService c = ControlService.instance;
        if (c != null) c.nav(action);
    }

    @JavascriptInterface public void typeText(String s) {
        ControlService c = ControlService.instance;
        if (c != null) c.typeText(s);
    }

    @JavascriptInterface public void key(String name) {
        ControlService c = ControlService.instance;
        if (c != null) c.key(name);
    }

    // ---- incoming request: get the owner's attention
    @JavascriptInterface public void notifyRequest(String who) {
        try {
            Vibrator v = (Vibrator) act.getSystemService(Context.VIBRATOR_SERVICE);
            if (v != null) {
                if (Build.VERSION.SDK_INT >= 26) v.vibrate(VibrationEffect.createOneShot(400, VibrationEffect.DEFAULT_AMPLITUDE));
                else v.vibrate(400);
            }
            NotificationManager nm = (NotificationManager) act.getSystemService(Context.NOTIFICATION_SERVICE);
            Notification.Builder b;
            if (Build.VERSION.SDK_INT >= 26) {
                nm.createNotificationChannel(new NotificationChannel("linkdesk_requests", "Connection requests",
                        NotificationManager.IMPORTANCE_HIGH));
                b = new Notification.Builder(act, "linkdesk_requests");
            } else {
                b = new Notification.Builder(act).setPriority(Notification.PRIORITY_HIGH);
            }
            PendingIntent open = PendingIntent.getActivity(act, 1,
                    new Intent(act, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT),
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            b.setContentTitle(who + " wants to control this phone")
                    .setContentText("Open LinkDesk to accept or decline")
                    .setSmallIcon(R.drawable.ic_stat).setContentIntent(open).setAutoCancel(true);
            if (Build.VERSION.SDK_INT >= 26) b.setTimeoutAfter(30_000);
            nm.notify(2, b.build());
        } catch (Exception ignored) { }
    }

    // ---- files received from the controller → Downloads/LinkDesk
    @JavascriptInterface public int fileStart(String name) {
        String safe = new File(name).getName();
        if (safe.isEmpty()) safe = "file";
        try {
            OutputStream out;
            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues cv = new ContentValues();
                cv.put(MediaStore.MediaColumns.DISPLAY_NAME, safe);
                cv.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/LinkDesk");
                Uri uri = act.getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
                out = act.getContentResolver().openOutputStream(uri);
            } else {
                File dir = new File(act.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "LinkDesk");
                dir.mkdirs();
                out = new FileOutputStream(new File(dir, safe));
            }
            synchronized (files) {
                int id = nextFile++;
                files.put(id, out);
                fileNames.put(id, safe);
                return id;
            }
        } catch (Exception e) {
            return 0;
        }
    }

    @JavascriptInterface public void fileChunk(int id, String b64) {
        OutputStream out;
        synchronized (files) { out = files.get(id); }
        if (out == null) return;
        try { out.write(Base64.decode(b64, Base64.DEFAULT)); } catch (Exception ignored) { }
    }

    @JavascriptInterface public String fileEnd(int id) {
        OutputStream out;
        String name;
        synchronized (files) { out = files.remove(id); name = fileNames.remove(id); }
        if (out == null) return "(could not save)";
        try { out.close(); } catch (Exception ignored) { }
        return (Build.VERSION.SDK_INT >= 29 ? "Download/LinkDesk/" : "LinkDesk app folder/") + name;
    }
}
