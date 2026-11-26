package io.github.prone.pocketpads;

import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanFilter;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.Context;
import android.media.midi.MidiDevice;
import android.media.midi.MidiDeviceInfo;
import android.media.midi.MidiInputPort;
import android.media.midi.MidiManager;
import android.media.midi.MidiOutputPort;
import android.media.midi.MidiReceiver;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.ParcelUuid;

import java.io.ByteArrayOutputStream;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * MIDI for PocketPads on Android. Python cannot subclass Android's classes, so this one does the listening
 * and Python collects the bytes with poll() and pollBle().
 *
 * USB (and other system) MIDI devices go through MidiManager. Bluetooth controllers are read directly over
 * GATT instead of MidiManager.openBluetoothDevice: on the device this was written on, that route connected an
 * M-Vave SMC-PAD and then delivered nothing from it. Reading them ourselves also gives us the SMC-PAD's
 * second channel (service AE40), which is how its pad lights are set.
 */
public class PPMidi {
    private static final ParcelUuid MIDI_SERVICE = ParcelUuid.fromString("03b80e5a-ede8-4b33-a751-6ce34ec4c700");
    private static final UUID MIDI_CHAR = UUID.fromString("7772e5db-3868-4112-a1a9-f2669d106bf3");
    private static final UUID CLIENT_CONFIG = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    private static final UUID SMC_SERVICE = UUID.fromString("0000ae40-0000-1000-8000-00805f9b34fb");
    private static final UUID SMC_WRITE = UUID.fromString("0000ae41-0000-1000-8000-00805f9b34fb");
    private static final UUID SMC_NOTIFY = UUID.fromString("0000ae42-0000-1000-8000-00805f9b34fb");
    private static final int MAX_WAITING = 256;                          // writes queued for one controller

    /** One Bluetooth controller. Android does one GATT operation at a time, so writes wait their turn in ops. */
    private static class Link {
        BluetoothGatt gatt;
        String name = "Bluetooth MIDI";
        boolean ready;                                                   // its MIDI channel is switched on
        boolean busy;
        BluetoothGattCharacteristic midi, smcWrite;
        final ArrayDeque<Object[]> ops = new ArrayDeque<Object[]>();     // {characteristic or descriptor, value}
        final ConcurrentLinkedQueue<byte[]> smcIn = new ConcurrentLinkedQueue<byte[]>();
    }

    private static final ConcurrentLinkedQueue<byte[]> in = new ConcurrentLinkedQueue<byte[]>();    // USB: raw MIDI
    private static final ConcurrentLinkedQueue<byte[]> ble = new ConcurrentLinkedQueue<byte[]>();   // Bluetooth packets
    private static final Map<Integer, MidiDevice> open = new HashMap<Integer, MidiDevice>();
    private static final Map<Integer, String> names = new HashMap<Integer, String>();
    // Every port we use. Android closes a port when its object is garbage collected, so they are kept here.
    private static final List<MidiOutputPort> held = new ArrayList<MidiOutputPort>();
    private static final Map<Integer, List<MidiInputPort>> outs = new HashMap<Integer, List<MidiInputPort>>();
    private static final Map<String, Link> links = new HashMap<String, Link>();                     // by address
    private static final Set<String> seen = new HashSet<String>();       // named devices the last scan heard, for the log
    private static Context ctx;
    private static MidiManager mm;
    private static Handler handler;
    private static BluetoothLeScanner scanner;
    private static ScanCallback scanCallback;
    private static volatile String note = "";

    /** Open what is there now and what is plugged in later. Returns "" or why MIDI is not available. */
    public static synchronized String start(Context context) {
        if (mm != null) return "";
        ctx = context.getApplicationContext();
        MidiManager m = (MidiManager) ctx.getSystemService(Context.MIDI_SERVICE);
        if (m == null) return "this device has no MIDI";
        HandlerThread t = new HandlerThread("ppmidi");
        t.start();
        handler = new Handler(t.getLooper());
        mm = m;
        mm.registerDeviceCallback(new MidiManager.DeviceCallback() {
            @Override public void onDeviceAdded(MidiDeviceInfo info) { openInfo(info); }
            @Override public void onDeviceRemoved(MidiDeviceInfo info) { forget(info.getId()); }
        }, handler);
        for (MidiDeviceInfo info : mm.getDevices()) openInfo(info);
        return "";
    }

    // ------------------------------------------------------------------ USB and other system devices
    private static void openInfo(MidiDeviceInfo info) {
        if (info.getType() == MidiDeviceInfo.TYPE_BLUETOOTH) return;     // those we talk to directly, see openBluetooth
        if (info.getOutputPortCount() == 0 && info.getInputPortCount() == 0) return;
        synchronized (PPMidi.class) { if (open.containsKey(info.getId())) return; }
        mm.openDevice(info, new MidiManager.OnDeviceOpenedListener() {
            @Override public void onDeviceOpened(MidiDevice device) { attach(device); }
        }, handler);
    }

    private static void attach(MidiDevice device) {
        if (device == null) return;
        MidiDeviceInfo info = device.getInfo();
        synchronized (PPMidi.class) {
            if (open.containsKey(info.getId())) return;
            open.put(info.getId(), device);
        }
        Bundle props = info.getProperties();
        String name = props.getString(MidiDeviceInfo.PROPERTY_NAME);
        if (name == null || name.length() == 0) name = props.getString(MidiDeviceInfo.PROPERTY_PRODUCT);
        if (name == null || name.length() == 0) name = "MIDI device";
        int ports = 0;
        List<MidiInputPort> toDevice = new ArrayList<MidiInputPort>();
        for (MidiDeviceInfo.PortInfo p : info.getPorts()) {
            if (p.getType() == MidiDeviceInfo.PortInfo.TYPE_OUTPUT) {    // "output" is the device's: what it sends us
                MidiOutputPort port = device.openOutputPort(p.getPortNumber());
                if (port == null) continue;
                port.connect(new MidiReceiver() {
                    @Override public void onSend(byte[] msg, int offset, int count, long timestamp) {
                        in.add(Arrays.copyOfRange(msg, offset, offset + count));
                    }
                });
                synchronized (PPMidi.class) { held.add(port); }
                ports++;
            } else {
                MidiInputPort port = device.openInputPort(p.getPortNumber());
                if (port != null) { toDevice.add(port); ports++; }       // null: another app has it
            }
        }
        synchronized (PPMidi.class) {
            if (ports > 0) names.put(info.getId(), name);
            outs.put(info.getId(), toDevice);
        }
        note = "opened " + name;
    }

    private static void forget(int id) {
        MidiDevice device;
        synchronized (PPMidi.class) {
            device = open.remove(id);
            names.remove(id);
            outs.remove(id);
        }
        if (device != null) {
            try { device.close(); } catch (Exception e) { /* already gone */ }
        }
    }

    /** USB: everything received since the last call, as one run of raw MIDI bytes; null when there is nothing. */
    public static byte[] poll() {
        byte[] first = in.poll();
        if (first == null) return null;
        byte[] next = in.poll();
        if (next == null) return first;
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(first, 0, first.length);
        while (next != null) {
            out.write(next, 0, next.length);
            next = in.poll();
        }
        return out.toByteArray();
    }

    /** Bluetooth MIDI packets received since the last call, each behind two bytes of length (low, high); null when none. */
    public static byte[] pollBle() {
        byte[] p = ble.poll();
        if (p == null) return null;
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        while (p != null) {
            out.write(p.length & 255);
            out.write(p.length >> 8);
            out.write(p, 0, p.length);
            p = ble.poll();
        }
        return out.toByteArray();
    }

    /** Send one MIDI message (raw bytes, status first) to every device we have open. */
    public static void send(byte[] msg) {
        List<MidiInputPort> ports = new ArrayList<MidiInputPort>();
        List<Link> bt;
        synchronized (PPMidi.class) {
            for (List<MidiInputPort> l : outs.values()) ports.addAll(l);
            bt = new ArrayList<Link>(links.values());
        }
        for (MidiInputPort p : ports) {
            try { p.send(msg, 0, msg.length); } catch (Exception e) { /* unplugged this instant */ }
        }
        if (bt.isEmpty()) return;
        byte[] packet = new byte[msg.length + 2];                        // Bluetooth MIDI: header, timestamp, message
        packet[0] = (byte) 0x80;
        packet[1] = (byte) 0x80;
        System.arraycopy(msg, 0, packet, 2, msg.length);
        for (Link l : bt) {
            if (l.ready && l.midi != null) write(l, l.midi, packet);
        }
    }

    /** Names of the devices we are connected to, one per line. */
    public static synchronized String devices() {
        List<String> all = new ArrayList<String>(names.values());
        for (Link l : links.values()) if (l.ready) all.add(l.name);
        StringBuilder sb = new StringBuilder();
        for (String n : all) {
            if (sb.length() > 0) sb.append('\n');
            sb.append(n);
        }
        return sb.toString();
    }

    /** What happened last, for the log; "" when nothing did. */
    public static String note() { return note; }

    // ------------------------------------------------------------------ Bluetooth controllers
    /** Look for Bluetooth MIDI controllers for a few seconds and open the ones found. Needs the Bluetooth permissions. */
    public static synchronized String scan(final int seconds) {
        if (mm == null) return "MIDI is not started";
        try {
            BluetoothManager bm = (BluetoothManager) ctx.getSystemService(Context.BLUETOOTH_SERVICE);
            BluetoothAdapter adapter = bm == null ? null : bm.getAdapter();
            if (adapter == null || !adapter.isEnabled()) return "Bluetooth is off";
            if (scanner != null) return "";                              // already looking
            seen.clear();
            // A controller paired in the system's Bluetooth settings is connected there and no longer
            // broadcasts, so a scan never hears it: open the paired ones that are (or may be) MIDI directly.
            for (BluetoothDevice dev : adapter.getBondedDevices()) {
                if (dev.getType() == BluetoothDevice.DEVICE_TYPE_CLASSIC) continue;
                ParcelUuid[] uuids = dev.getUuids();
                boolean midi = uuids == null || uuids.length == 0;       // not known yet: worth a try
                if (uuids != null) for (ParcelUuid u : uuids) if (MIDI_SERVICE.equals(u)) midi = true;
                if (midi) openBluetooth(dev);
            }
            final BluetoothLeScanner s = adapter.getBluetoothLeScanner();
            if (s == null) return "Bluetooth is off";
            // No filter on the scan itself: some controllers name the MIDI service only in the second half
            // of what they broadcast, which a filtered scan does not always match. We check each result.
            List<ScanFilter> filters = new ArrayList<ScanFilter>();
            scanCallback = new ScanCallback() {
                @Override public void onScanResult(int type, ScanResult result) {
                    final BluetoothDevice dev = result.getDevice();
                    boolean midi = false;
                    String name = null;
                    if (result.getScanRecord() != null) {
                        name = result.getScanRecord().getDeviceName();
                        List<ParcelUuid> uuids = result.getScanRecord().getServiceUuids();
                        midi = uuids != null && uuids.contains(MIDI_SERVICE);
                    }
                    synchronized (PPMidi.class) {
                        if (name != null && seen.add(name + (midi ? " (midi)" : ""))) note = "seen: " + seen;
                    }
                    if (midi) openBluetooth(dev);
                }
                @Override public void onScanFailed(int errorCode) { note = "scan failed: " + errorCode; }
            };
            s.startScan(filters, new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(), scanCallback);
            scanner = s;
            handler.postDelayed(new Runnable() {
                @Override public void run() { stopScan(); }
            }, seconds * 1000L);
            return "";
        } catch (SecurityException e) {
            return "Bluetooth is not allowed for PocketPads";
        } catch (Exception e) {
            return "Bluetooth: " + e;
        }
    }

    private static void openBluetooth(final BluetoothDevice dev) {
        final Link link = new Link();
        synchronized (PPMidi.class) {
            if (links.containsKey(dev.getAddress())) return;             // open, or being opened
            links.put(dev.getAddress(), link);
        }
        try {
            link.gatt = dev.connectGatt(ctx, false, new BluetoothGattCallback() {
                @Override public void onConnectionStateChange(BluetoothGatt gatt, int status, int newState) {
                    if (newState == BluetoothProfile.STATE_CONNECTED) {
                        // Bigger packets first: the SMC-PAD's settings come a kilobyte at a time.
                        if (!gatt.requestMtu(517)) gatt.discoverServices();
                    } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                        dropBluetooth(gatt, "gone (" + status + ")");
                    }
                }
                @Override public void onMtuChanged(BluetoothGatt gatt, int mtu, int status) {
                    gatt.discoverServices();
                }
                @Override public void onServicesDiscovered(BluetoothGatt gatt, int status) {
                    BluetoothGattService svc = gatt.getService(MIDI_SERVICE.getUuid());
                    BluetoothGattCharacteristic ch = svc == null ? null : svc.getCharacteristic(MIDI_CHAR);
                    if (ch == null) {                                    // not a MIDI controller after all
                        dropBluetooth(gatt, "no MIDI service");
                        return;
                    }
                    String name = gatt.getDevice().getName();
                    if (name != null) link.name = name;
                    link.midi = ch;
                    ch.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE);
                    listen(gatt, link, ch);
                    BluetoothGattService smc = gatt.getService(SMC_SERVICE);
                    BluetoothGattCharacteristic w = smc == null ? null : smc.getCharacteristic(SMC_WRITE);
                    BluetoothGattCharacteristic n = smc == null ? null : smc.getCharacteristic(SMC_NOTIFY);
                    if (w != null && n != null) {
                        w.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE);
                        listen(gatt, link, n);
                        link.smcWrite = w;
                    }
                    gatt.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH);   // pads want low delay
                    link.ready = true;
                    note = "opened " + link.name + (link.smcWrite != null ? " (with lights)" : "");
                }
                @Override public void onCharacteristicChanged(BluetoothGatt gatt, BluetoothGattCharacteristic ch) {
                    byte[] v = ch.getValue();
                    if (v == null) return;
                    if (MIDI_CHAR.equals(ch.getUuid())) ble.add(v.clone());
                    else if (SMC_NOTIFY.equals(ch.getUuid())) link.smcIn.add(v.clone());
                }
                @Override public void onDescriptorWrite(BluetoothGatt gatt, BluetoothGattDescriptor d, int status) {
                    done(link);
                }
                @Override public void onCharacteristicWrite(BluetoothGatt gatt, BluetoothGattCharacteristic ch, int status) {
                    done(link);
                }
            }, BluetoothDevice.TRANSPORT_LE);
            if (link.gatt == null) throw new IllegalStateException("no connection");
        } catch (Exception e) {
            note = "open: " + e;
            synchronized (PPMidi.class) { links.remove(dev.getAddress()); }
        }
    }

    /** Ask to be told when this characteristic changes. */
    private static void listen(BluetoothGatt gatt, Link link, BluetoothGattCharacteristic ch) {
        gatt.setCharacteristicNotification(ch, true);
        BluetoothGattDescriptor d = ch.getDescriptor(CLIENT_CONFIG);
        if (d != null) write(link, d, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
    }

    private static void write(Link link, Object target, byte[] value) {
        synchronized (link) {
            if (link.ops.size() >= MAX_WAITING) return;                  // it cannot keep up: drop, do not pile up
            link.ops.add(new Object[] {target, value});
        }
        pump(link);
    }

    private static void done(Link link) {
        synchronized (link) { link.busy = false; }
        pump(link);
    }

    private static void pump(Link link) {
        synchronized (link) {
            while (!link.busy && !link.ops.isEmpty() && link.gatt != null) {
                Object[] op = link.ops.poll();
                boolean ok;
                try {
                    if (op[0] instanceof BluetoothGattDescriptor) {
                        BluetoothGattDescriptor d = (BluetoothGattDescriptor) op[0];
                        d.setValue((byte[]) op[1]);
                        ok = link.gatt.writeDescriptor(d);
                    } else {
                        BluetoothGattCharacteristic c = (BluetoothGattCharacteristic) op[0];
                        c.setValue((byte[]) op[1]);
                        ok = link.gatt.writeCharacteristic(c);
                    }
                } catch (Exception e) {
                    ok = false;
                }
                link.busy = ok;                                          // refused: skip it and try the next
            }
        }
    }

    private static void dropBluetooth(BluetoothGatt gatt, String why) {
        String addr = gatt.getDevice().getAddress();
        synchronized (PPMidi.class) {
            Link l = links.remove(addr);
            if (l != null && l.ready && why.length() > 0) note = l.name + ": " + why;
        }
        try { gatt.close(); } catch (Exception e) { /* already closed */ }
    }

    // ------------------------------------------------------------------ the SMC-PAD's lights channel
    /** Controllers whose pad lights can be set: "address<tab>name" per line. */
    public static synchronized String lights() {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, Link> e : links.entrySet()) {
            if (!e.getValue().ready || e.getValue().smcWrite == null) continue;
            if (sb.length() > 0) sb.append('\n');
            sb.append(e.getKey()).append('\t').append(e.getValue().name);
        }
        return sb.toString();
    }

    private static Link lightsOf(String addr) {
        Link l;
        synchronized (PPMidi.class) { l = links.get(addr); }
        return l != null && l.ready && l.smcWrite != null ? l : null;
    }

    /** Send one packet on the lights channel. False when that controller is gone. */
    public static boolean smcWrite(String addr, byte[] packet) {
        Link l = lightsOf(addr);
        if (l == null) return false;
        write(l, l.smcWrite, packet.clone());
        return true;
    }

    /** What the controller answered on the lights channel since the last call; empty when nothing, null when it is gone. */
    public static byte[] smcPoll(String addr) {
        Link l = lightsOf(addr);
        if (l == null) return null;
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] p = l.smcIn.poll();
        while (p != null) {
            out.write(p, 0, p.length);
            p = l.smcIn.poll();
        }
        return out.toByteArray();
    }

    private static synchronized void stopScan() {
        if (scanner != null) {
            try { scanner.stopScan(scanCallback); } catch (Exception e) { /* Bluetooth went off */ }
            scanner = null;
        }
    }

    /** Let go of every device (they are free for other apps again). */
    public static void stop() {
        stopScan();
        List<Integer> ids;
        List<Link> bt;
        synchronized (PPMidi.class) {
            ids = new ArrayList<Integer>(open.keySet());
            bt = new ArrayList<Link>(links.values());
        }
        for (int id : ids) forget(id);
        for (Link l : bt) {
            if (l.gatt == null) continue;
            try { l.gatt.disconnect(); } catch (Exception e) { /* Bluetooth went off */ }
            dropBluetooth(l.gatt, "");
        }
    }
}
