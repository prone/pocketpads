package io.github.prone.pocketpads;

import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.BluetoothManager;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanFilter;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.Context;
import android.media.midi.MidiDevice;
import android.media.midi.MidiDeviceInfo;
import android.media.midi.MidiManager;
import android.media.midi.MidiOutputPort;
import android.media.midi.MidiReceiver;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.ParcelUuid;

import java.io.ByteArrayOutputStream;
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
 * MIDI in for PocketPads on Android. Python cannot subclass Android's MidiReceiver, so this class opens every
 * MIDI device (USB ones as they appear, Bluetooth ones found by scan()), and queues what they send as raw
 * MIDI bytes for Python to collect with poll().
 */
public class PPMidi {
    private static final ParcelUuid MIDI_SERVICE = ParcelUuid.fromString("03b80e5a-ede8-4b33-a751-6ce34ec4c700");
    private static final ConcurrentLinkedQueue<byte[]> in = new ConcurrentLinkedQueue<byte[]>();
    private static final Map<Integer, MidiDevice> open = new HashMap<Integer, MidiDevice>();
    private static final Map<Integer, String> names = new HashMap<Integer, String>();
    // Every port we listen on. Android closes a port when its object is garbage collected, so they are kept here.
    private static final List<MidiOutputPort> held = new ArrayList<MidiOutputPort>();
    private static final Set<String> asked = new HashSet<String>();      // Bluetooth addresses being opened
    private static final UUID MIDI_CHAR = UUID.fromString("7772e5db-3868-4112-a1a9-f2669d106bf3");
    private static final UUID CLIENT_CONFIG = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    private static final ConcurrentLinkedQueue<byte[]> ble = new ConcurrentLinkedQueue<byte[]>();
    private static final Map<String, BluetoothGatt> gatts = new HashMap<String, BluetoothGatt>();
    private static final Map<String, String> btNames = new HashMap<String, String>();
    private static final Set<String> seen = new HashSet<String>();       // named devices the last scan heard, for the log
    private static Context ctx;
    private static MidiManager mm;
    private static Handler handler;
    private static BluetoothLeScanner scanner;
    private static ScanCallback scanCallback;
    private static volatile String note = "";
    private static volatile long received = 0;

    /** Bytes received since the start, to tell "nothing arrives" from "arrives and is not understood". */
    public static long received() { return received; }

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

    private static void openInfo(MidiDeviceInfo info) {
        if (info.getOutputPortCount() == 0) return;                      // nothing to listen to
        if (info.getType() == MidiDeviceInfo.TYPE_BLUETOOTH) return;     // those we talk to directly, see openBluetooth
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
        for (MidiDeviceInfo.PortInfo p : info.getPorts()) {
            if (p.getType() != MidiDeviceInfo.PortInfo.TYPE_OUTPUT) continue;
            MidiOutputPort port = device.openOutputPort(p.getPortNumber());
            if (port == null) continue;
            port.connect(new MidiReceiver() {
                @Override public void onSend(byte[] msg, int offset, int count, long timestamp) {
                    in.add(Arrays.copyOfRange(msg, offset, offset + count));
                    received += count;
                }
            });
            synchronized (PPMidi.class) { held.add(port); }             // or it is collected, and closes itself
            ports++;
        }
        synchronized (PPMidi.class) { if (ports > 0) names.put(info.getId(), name); }
        note = "opened " + name + ": " + ports + " of " + info.getOutputPortCount() + " ports";
    }

    private static void forget(int id) {
        MidiDevice device;
        synchronized (PPMidi.class) {
            device = open.remove(id);
            names.remove(id);
        }
        if (device != null) {
            try {
                BluetoothDevice bt = (BluetoothDevice) device.getInfo().getProperties()
                        .getParcelable(MidiDeviceInfo.PROPERTY_BLUETOOTH_DEVICE);
                if (bt != null) synchronized (PPMidi.class) { asked.remove(bt.getAddress()); }
                device.close();
            } catch (Exception e) { /* already gone */ }
        }
    }

    /** Everything received since the last call, as one run of raw MIDI bytes; null when there is nothing. */
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

    /** Names of the devices we are listening to, one per line. */
    public static synchronized String devices() {
        StringBuilder sb = new StringBuilder();
        List<String> all = new ArrayList<String>(names.values());
        all.addAll(btNames.values());
        for (String n : all) {
            if (sb.length() > 0) sb.append('\n');
            sb.append(n);
        }
        return sb.toString();
    }

    /** What went wrong last, for the log; "" when nothing did. */
    public static String note() { return note; }

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

    // Bluetooth controllers are read directly, not through MidiManager.openBluetoothDevice: on the device
    // this was written on, that route connected an M-Vave SMC-PAD and then delivered nothing from it.
    private static void openBluetooth(final BluetoothDevice dev) {
        synchronized (PPMidi.class) {
            if (!asked.add(dev.getAddress())) return;                    // open, or being opened
        }
        try {
            BluetoothGatt g = dev.connectGatt(ctx, false, new BluetoothGattCallback() {
                @Override public void onConnectionStateChange(BluetoothGatt gatt, int status, int newState) {
                    if (newState == BluetoothProfile.STATE_CONNECTED) {
                        gatt.discoverServices();
                    } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                        dropBluetooth(gatt, "gone (" + status + ")");
                    }
                }
                @Override public void onServicesDiscovered(BluetoothGatt gatt, int status) {
                    BluetoothGattService svc = gatt.getService(MIDI_SERVICE.getUuid());
                    BluetoothGattCharacteristic ch = svc == null ? null : svc.getCharacteristic(MIDI_CHAR);
                    if (ch == null) {                                    // not a MIDI controller after all
                        dropBluetooth(gatt, "no MIDI service");
                        return;
                    }
                    gatt.setCharacteristicNotification(ch, true);
                    BluetoothGattDescriptor d = ch.getDescriptor(CLIENT_CONFIG);
                    if (d != null) {
                        d.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
                        gatt.writeDescriptor(d);
                    }
                    gatt.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH);   // pads want low delay
                    String name = gatt.getDevice().getName();
                    synchronized (PPMidi.class) {
                        btNames.put(gatt.getDevice().getAddress(), name == null ? "Bluetooth MIDI" : name);
                    }
                    note = "opened " + name;
                }
                @Override public void onCharacteristicChanged(BluetoothGatt gatt, BluetoothGattCharacteristic ch) {
                    byte[] v = ch.getValue();
                    if (v != null && MIDI_CHAR.equals(ch.getUuid())) {
                        ble.add(v.clone());
                        received += v.length;
                    }
                }
            }, BluetoothDevice.TRANSPORT_LE);
            synchronized (PPMidi.class) { if (g != null) gatts.put(dev.getAddress(), g); }
        } catch (Exception e) {
            note = "open: " + e;
            synchronized (PPMidi.class) { asked.remove(dev.getAddress()); }
        }
    }

    private static void dropBluetooth(BluetoothGatt gatt, String why) {
        String addr = gatt.getDevice().getAddress();
        synchronized (PPMidi.class) {
            if (btNames.remove(addr) != null) note = why;
            gatts.remove(addr);
            asked.remove(addr);
        }
        try { gatt.close(); } catch (Exception e) { /* already closed */ }
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
        synchronized (PPMidi.class) { ids = new ArrayList<Integer>(open.keySet()); }
        for (int id : ids) forget(id);
        List<BluetoothGatt> gs;
        synchronized (PPMidi.class) { gs = new ArrayList<BluetoothGatt>(gatts.values()); }
        for (BluetoothGatt g : gs) {
            try { g.disconnect(); } catch (Exception e) { /* Bluetooth went off */ }
            dropBluetooth(g, "");
        }
    }
}
