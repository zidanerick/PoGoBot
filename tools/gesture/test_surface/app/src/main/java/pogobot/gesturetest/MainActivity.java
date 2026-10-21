package pogobot.gesturetest;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.util.Log;
import android.view.MotionEvent;
import android.widget.TextView;

import java.util.Locale;

/** Harmless foreground surface for observing injector MotionEvents. */
public final class MainActivity extends Activity {
    private static final String TAG = "GestureTouchLog";
    private TextView output;
    private final StringBuilder history = new StringBuilder();

    @Override
    public void onCreate(Bundle state) {
        super.onCreate(state);
        output = new TextView(this);
        output.setTextColor(Color.WHITE);
        output.setBackgroundColor(Color.rgb(24, 39, 58));
        output.setTextSize(16);
        output.setPadding(24, 48, 24, 24);
        output.setText("Gesture test surface\nLeave this app foregrounded.\nEvents appear below.");
        output.setOnTouchListener((view, event) -> {
            record(event);
            return true;
        });
        setContentView(output);
    }

    private void record(MotionEvent event) {
        String action = actionName(event.getActionMasked());
        StringBuilder row = new StringBuilder(String.format(Locale.US,
                "%s pointers=%d down=%d event=%d",
                action, event.getPointerCount(), event.getDownTime(), event.getEventTime()));
        for (int i = 0; i < event.getPointerCount(); i++) {
            row.append(String.format(Locale.US, " id%d=(%.1f,%.1f)",
                    event.getPointerId(i), event.getX(i), event.getY(i)));
        }
        String line = row.toString();
        Log.i(TAG, line);
        history.append(line).append('\n');
        if (history.length() > 12000) history.delete(0, history.length() - 9000);
        output.setText("Gesture test surface\nLeave this app foregrounded.\n\n" + history);
    }

    private static String actionName(int action) {
        switch (action) {
            case MotionEvent.ACTION_DOWN: return "DOWN";
            case MotionEvent.ACTION_UP: return "UP";
            case MotionEvent.ACTION_MOVE: return "MOVE";
            case MotionEvent.ACTION_CANCEL: return "CANCEL";
            case MotionEvent.ACTION_POINTER_DOWN: return "POINTER_DOWN";
            case MotionEvent.ACTION_POINTER_UP: return "POINTER_UP";
            default: return "OTHER(" + action + ")";
        }
    }
}
