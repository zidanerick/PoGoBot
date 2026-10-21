package pogobot;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/** Continuous single-pointer MotionEvent trajectory for app_process on Android. */
public final class Gesture {
    private static Object manager;
    private static Method injector;
    private static Class<?> motionEvent;
    private static Class<?> pointerProperties;
    private static Class<?> pointerCoords;
    private static final BlockingQueue<String> input = new ArrayBlockingQueue<>(4);
    private static final AtomicBoolean cancel = new AtomicBoolean(false);
    private static long lastEventTime;

    private static final class Point {
        final int t, x, y;
        Point(int t, int x, int y) { this.t = t; this.x = x; this.y = y; }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1 || !"--stdio".equals(args[0]))
            throw new IllegalArgumentException("expected --stdio");
        initInjector();
        Thread reader = new Thread(() -> {
            try {
                BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
                String line;
                while ((line = in.readLine()) != null) input.put(line);
                input.put("EOF");
            } catch (Exception e) {
                try { input.put("EOF"); } catch (InterruptedException ignored) { }
            }
        }, "gesture-stdin");
        reader.setDaemon(true);
        reader.start();
        System.out.println("READY");
        System.out.flush();

        String plan = input.take();
        if ("EOF".equals(plan)) return;
        if (!plan.startsWith("PLAN ")) throw new IllegalArgumentException("expected PLAN");
        Point[] points = parse(plan.substring(5));
        Thread motion = new Thread(() -> run(points), "gesture-motion");
        motion.start();
        while (motion.isAlive()) {
            String command = input.poll(10, TimeUnit.MILLISECONDS);
            if (command == null) continue;
            if ("CANCEL".equals(command) || "EOF".equals(command)) cancel.set(true);
        }
        motion.join();
    }

    private static Point[] parse(String body) {
        String[] rows = body.split(";");
        if (rows.length < 2 || rows.length > 1001)
            throw new IllegalArgumentException("sample count outside 2..1001");
        Point[] points = new Point[rows.length];
        int previous = -1;
        for (int i = 0; i < rows.length; i++) {
            String[] values = rows[i].split(",");
            if (values.length != 3) throw new IllegalArgumentException("bad sample");
            int t = Integer.parseInt(values[0]);
            int x = Integer.parseInt(values[1]);
            int y = Integer.parseInt(values[2]);
            if (t <= previous || x < 0 || y < 0)
                throw new IllegalArgumentException("unordered time or negative coordinate");
            previous = t;
            points[i] = new Point(t, x, y);
        }
        if (points[0].t != 0 || previous < 16 || previous > 5000)
            throw new IllegalArgumentException("duration outside 16..5000ms");
        return points;
    }

    private static void initInjector() throws Exception {
        motionEvent = Class.forName("android.view.MotionEvent");
        pointerProperties = Class.forName("android.view.MotionEvent$PointerProperties");
        pointerCoords = Class.forName("android.view.MotionEvent$PointerCoords");
        Class<?> inputEvent = Class.forName("android.view.InputEvent");
        Class<?> cls;
        try {
            cls = Class.forName("android.hardware.input.InputManager");
            manager = cls.getMethod("getInstance").invoke(null);
        } catch (Throwable ignored) {
            cls = Class.forName("android.hardware.input.InputManagerGlobal");
            manager = cls.getMethod("getInstance").invoke(null);
        }
        injector = cls.getMethod("injectInputEvent", inputEvent, int.class);
    }

    private static void run(Point[] points) {
        boolean down = false;
        long downTime = 0;
        Point last = points[0];
        try {
            downTime = uptimeMillis();
            down = true;
            // Attempt CANCEL even if DOWN reports an injection error after reaching the
            // framework; a partial press must never be left hanging.
            send(downTime, MotionAction.DOWN, last);
            System.out.println("START " + downTime);
            System.out.flush();
            for (int i = 1; i < points.length; i++) {
                Point p = points[i];
                long deadline = downTime + p.t;
                while (!cancel.get()) {
                long remaining = deadline - uptimeMillis();
                    if (remaining <= 0) break;
                    Thread.sleep(Math.min(remaining, 5));
                }
                if (cancel.get()) break;
                long now = uptimeMillis();
                if (now - deadline > 40) throw new IllegalStateException("gesture late");
                send(downTime, MotionAction.MOVE, p);
                last = p;
            }
            if (!cancel.get()) {
                send(downTime, MotionAction.UP, last);
                down = false;
                System.out.println("DONE " + uptimeMillis());
            } else {
                send(downTime, MotionAction.CANCEL, last);
                down = false;
                System.out.println("CANCELLED " + uptimeMillis());
            }
        } catch (Throwable e) {
            if (down) {
                try { send(downTime, MotionAction.CANCEL, last); }
                catch (Throwable cleanup) { e.addSuppressed(cleanup); }
            }
            System.out.println("ERROR " + e.getClass().getSimpleName() + ": " + e.getMessage());
        }
        System.out.flush();
    }

    private static final class MotionAction {
        static final int DOWN = 0;
        static final int UP = 1;
        static final int MOVE = 2;
        static final int CANCEL = 3;
    }

    private static void send(long downTime, int action, Point point) throws Exception {
        Object props = Array.newInstance(pointerProperties, 1);
        Object prop = pointerProperties.getConstructor().newInstance();
        set(pointerProperties, prop, "id", 0);
        set(pointerProperties, prop, "toolType", 1); // TOOL_TYPE_FINGER
        Array.set(props, 0, prop);

        Object coords = Array.newInstance(pointerCoords, 1);
        Object coord = pointerCoords.getConstructor().newInstance();
        set(pointerCoords, coord, "x", (float) point.x);
        set(pointerCoords, coord, "y", (float) point.y);
        set(pointerCoords, coord, "pressure", 1f);
        set(pointerCoords, coord, "size", 1f);
        Array.set(coords, 0, coord);

        long eventTime = Math.max(uptimeMillis(), lastEventTime + 1);
        lastEventTime = eventTime;
        Method obtain = motionEvent.getMethod("obtain", long.class, long.class, int.class,
            int.class, props.getClass(), coords.getClass(), int.class, int.class,
            float.class, float.class, int.class, int.class, int.class, int.class);
        Object event = obtain.invoke(null, downTime, eventTime, action, 1, props, coords,
            0, 0, 1f, 1f, 0, 0, 0x1002 /* SOURCE_TOUCHSCREEN */, 0);
        try {
            Object accepted = injector.invoke(manager, event, 2 /* WAIT_FOR_FINISH */);
            if (accepted instanceof Boolean && !((Boolean) accepted))
                throw new IllegalStateException("InputManager rejected MotionEvent");
        } finally {
            motionEvent.getMethod("recycle").invoke(event);
        }
    }

    private static long uptimeMillis() throws Exception {
        Class<?> clock = Class.forName("android.os.SystemClock");
        return ((Long) clock.getMethod("uptimeMillis").invoke(null)).longValue();
    }

    private static void set(Class<?> owner, Object target, String name, Object value)
        throws Exception {
        Field field = owner.getField(name);
        field.set(target, value);
    }
}
