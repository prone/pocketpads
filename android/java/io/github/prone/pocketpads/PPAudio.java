package io.github.prone.pocketpads;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaCodec;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.media.MediaRecorder;

import java.io.File;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;

/**
 * The two audio jobs PocketPads hands to other programs on Linux (ffmpeg and arecord), done with Android's own
 * parts: turning a compressed file into a WAV, and recording the microphone into a WAV.
 */
public class PPAudio {
    private static volatile boolean recording;
    private static volatile boolean stopAsked;
    private static volatile String recError = "";

    private static void header(RandomAccessFile f, int rate, int channels, long bytes) throws java.io.IOException {
        ByteBuffer b = ByteBuffer.allocate(44).order(java.nio.ByteOrder.LITTLE_ENDIAN);
        b.put("RIFF".getBytes()).putInt((int) (36 + bytes)).put("WAVE".getBytes());
        b.put("fmt ".getBytes()).putInt(16).putShort((short) 1).putShort((short) channels).putInt(rate);
        b.putInt(rate * channels * 2).putShort((short) (channels * 2)).putShort((short) 16);
        b.put("data".getBytes()).putInt((int) bytes);
        f.seek(0);
        f.write(b.array());
    }

    /** Decode the first audio track of src (MP3, M4A, FLAC, OGG...) into a 16-bit WAV at dst. Returns "" or what went wrong. */
    public static String decode(String src, String dst, int maxSeconds) {
        MediaExtractor ex = new MediaExtractor();
        MediaCodec codec = null;
        RandomAccessFile out = null;
        try {
            ex.setDataSource(src);
            MediaFormat fmt = null;
            for (int i = 0; i < ex.getTrackCount(); i++) {
                MediaFormat f = ex.getTrackFormat(i);
                String mime = f.getString(MediaFormat.KEY_MIME);
                if (mime != null && mime.startsWith("audio/")) {
                    ex.selectTrack(i);
                    fmt = f;
                    break;
                }
            }
            if (fmt == null) return "not an audio file";
            codec = MediaCodec.createDecoderByType(fmt.getString(MediaFormat.KEY_MIME));
            codec.configure(fmt, null, null, 0);
            codec.start();
            int rate = fmt.getInteger(MediaFormat.KEY_SAMPLE_RATE);
            int channels = fmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
            out = new RandomAccessFile(dst, "rw");
            out.setLength(0);
            out.write(new byte[44]);
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            long written = 0, limit = Long.MAX_VALUE;
            boolean fed = false, done = false;
            int idle = 0;
            byte[] chunk = new byte[1 << 16];
            while (!done && written < limit) {
                if (!fed) {
                    int in = codec.dequeueInputBuffer(10000);
                    if (in >= 0) {
                        int n = ex.readSampleData(codec.getInputBuffer(in), 0);
                        if (n < 0) {
                            codec.queueInputBuffer(in, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            fed = true;
                        } else {
                            codec.queueInputBuffer(in, 0, n, ex.getSampleTime(), 0);
                            ex.advance();
                        }
                    }
                }
                int o = codec.dequeueOutputBuffer(info, 10000);
                if (o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat of = codec.getOutputFormat();
                    rate = of.getInteger(MediaFormat.KEY_SAMPLE_RATE);
                    channels = of.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
                    if (of.containsKey(MediaFormat.KEY_PCM_ENCODING)
                            && of.getInteger(MediaFormat.KEY_PCM_ENCODING) != AudioFormat.ENCODING_PCM_16BIT) {
                        return "a sound format this device decodes in a way we do not read";
                    }
                } else if (o >= 0) {
                    idle = 0;
                    limit = (long) maxSeconds * rate * channels * 2;
                    ByteBuffer buf = codec.getOutputBuffer(o);
                    buf.position(info.offset);
                    int left = (int) Math.min(info.size, limit - written);
                    while (left > 0) {
                        int n = Math.min(left, chunk.length);
                        buf.get(chunk, 0, n);
                        out.write(chunk, 0, n);
                        written += n;
                        left -= n;
                    }
                    codec.releaseOutputBuffer(o, false);
                    if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) done = true;
                } else if (fed && ++idle > 500) {
                    done = true;                                         // the decoder never said it was finished
                }
            }
            header(out, rate, channels, written);
            return written > 0 ? "" : "no sound in the file";
        } catch (Exception e) {
            return String.valueOf(e.getMessage() == null ? e : e.getMessage());
        } finally {
            try { if (codec != null) { codec.stop(); codec.release(); } } catch (Exception e) { /* never started */ }
            ex.release();
            try { if (out != null) out.close(); } catch (Exception e) { /* nothing to do */ }
        }
    }

    /** Record the microphone into a mono 48 kHz WAV at path, for up to seconds. Returns "" or why it cannot. */
    public static synchronized String record(final String path, final int seconds) {
        if (recording) return "already recording";
        final int rate = 48000;
        final int min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (min <= 0) return "this device has no microphone we can use";
        final AudioRecord rec;
        try {
            rec = new AudioRecord(MediaRecorder.AudioSource.MIC, rate, AudioFormat.CHANNEL_IN_MONO,
                                  AudioFormat.ENCODING_PCM_16BIT, Math.max(min, rate / 2));
        } catch (SecurityException e) {
            return "the microphone is not allowed for PocketPads";
        } catch (Exception e) {
            return String.valueOf(e.getMessage());
        }
        if (rec.getState() != AudioRecord.STATE_INITIALIZED) {
            rec.release();
            return "the microphone would not open";
        }
        recording = true;
        stopAsked = false;
        recError = "";
        new Thread(new Runnable() {
            @Override public void run() {
                RandomAccessFile out = null;
                long written = 0;
                try {
                    out = new RandomAccessFile(new File(path), "rw");
                    out.setLength(0);
                    header(out, rate, 1, 0);
                    rec.startRecording();
                    byte[] buf = new byte[4800];                         // a twentieth of a second
                    long limit = (long) seconds * rate * 2;
                    while (!stopAsked && written < limit) {
                        int n = rec.read(buf, 0, (int) Math.min(buf.length, limit - written));
                        if (n < 0) throw new java.io.IOException("the microphone stopped (" + n + ")");
                        out.write(buf, 0, n);
                        written += n;
                    }
                } catch (Exception e) {
                    recError = String.valueOf(e.getMessage() == null ? e : e.getMessage());
                } finally {
                    try { rec.stop(); } catch (Exception e) { /* never started */ }
                    rec.release();
                    try {
                        if (out != null) {
                            header(out, rate, 1, written);
                            out.close();
                        }
                    } catch (Exception e) { /* the file is what it is */ }
                    recording = false;
                }
            }
        }, "pprecord").start();
        return "";
    }

    public static void recordStop() { stopAsked = true; }

    public static boolean recording() { return recording; }

    /** What went wrong with the last recording; "" when nothing did. */
    public static String recordError() { return recError; }
}
