package io.linkdesk.app;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.os.Build;
import android.os.Bundle;
import android.util.DisplayMetrics;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

/** Performs taps, swipes, typing and Back/Home/Recents for the remote person.
 *  Only active after the phone's owner turns it on in Settings → Accessibility. */
public class ControlService extends AccessibilityService {
    static volatile ControlService instance;

    @Override
    protected void onServiceConnected() { instance = this; }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) { }

    @Override
    public void onInterrupt() { }

    @Override
    public void onDestroy() {
        if (instance == this) instance = null;
        super.onDestroy();
    }

    private float[] screen() {
        DisplayMetrics dm = new DisplayMetrics();
        ((WindowManager) getSystemService(WINDOW_SERVICE)).getDefaultDisplay().getRealMetrics(dm);
        return new float[]{dm.widthPixels, dm.heightPixels};
    }

    private float px(double v, float max) {
        return (float) Math.min(Math.max(v, 0), 1) * (max - 1);
    }

    private void run(Path path, long duration) {
        GestureDescription.Builder b = new GestureDescription.Builder();
        b.addStroke(new GestureDescription.StrokeDescription(path, 0, Math.max(1, duration)));
        dispatchGesture(b.build(), null, null);
    }

    void tap(double x, double y) {
        float[] s = screen();
        Path p = new Path();
        p.moveTo(px(x, s[0]), px(y, s[1]));
        run(p, 50);
    }

    void longPress(double x, double y) {
        float[] s = screen();
        Path p = new Path();
        p.moveTo(px(x, s[0]), px(y, s[1]));
        run(p, 700);
    }

    void swipe(double[][] pts, long duration) {
        if (pts.length == 0) return;
        float[] s = screen();
        Path p = new Path();
        p.moveTo(px(pts[0][0], s[0]), px(pts[0][1], s[1]));
        for (int i = 1; i < pts.length; i++) p.lineTo(px(pts[i][0], s[0]), px(pts[i][1], s[1]));
        if (pts.length == 1) p.lineTo(px(pts[0][0], s[0]) + 1, px(pts[0][1], s[1]) + 1);
        run(p, Math.min(duration, 5000));
    }

    /** Mouse wheel / two-finger scroll: dy > 0 scrolls up (finger moves down). */
    void scroll(double x, double y, double dy) {
        float[] s = screen();
        float sx = px(x, s[0]), sy = px(y, s[1]);
        float dist = (float) (Math.signum(dy) * s[1] * 0.15);
        float ey = Math.min(Math.max(sy + dist, 1), s[1] - 2);
        Path p = new Path();
        p.moveTo(sx, sy);
        p.lineTo(sx, ey);
        run(p, 220);
    }

    void nav(String action) {
        switch (action) {
            case "back": performGlobalAction(GLOBAL_ACTION_BACK); break;
            case "home": performGlobalAction(GLOBAL_ACTION_HOME); break;
            case "recents": performGlobalAction(GLOBAL_ACTION_RECENTS); break;
            default: break;
        }
    }

    private AccessibilityNodeInfo focusedField() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return null;
        AccessibilityNodeInfo n = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        return (n != null && n.isEditable()) ? n : null;
    }

    private String currentText(AccessibilityNodeInfo n) {
        if (Build.VERSION.SDK_INT >= 26 && n.isShowingHintText()) return "";
        CharSequence t = n.getText();
        return t == null ? "" : t.toString();
    }

    private void setText(AccessibilityNodeInfo n, String text) {
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        n.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        Bundle sel = new Bundle();
        sel.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, text.length());
        sel.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, text.length());
        n.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, sel);
    }

    /** Types into the field that currently has focus (adds at the end). */
    void typeText(String s) {
        AccessibilityNodeInfo n = focusedField();
        if (n != null) setText(n, currentText(n) + s);
    }

    void key(String name) {
        AccessibilityNodeInfo n = focusedField();
        if (n == null) return;
        String cur = currentText(n);
        if (("Backspace".equals(name) || "Delete".equals(name)) && !cur.isEmpty()) {
            int end = cur.offsetByCodePoints(cur.length(), -1);
            setText(n, cur.substring(0, end));
        } else if ("Enter".equals(name)) {
            if (Build.VERSION.SDK_INT >= 30) {
                n.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.getId());
            } else if (n.isMultiLine()) {
                setText(n, cur + "\n");
            }
        }
    }
}
