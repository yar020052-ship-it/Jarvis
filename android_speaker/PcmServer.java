package com.jarvis;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;

import java.io.InputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * Plays raw 16-bit PCM streamed over TCP through the device speaker.
 *
 * stdin is unusable on Windows (adb.exe treats 0x1A as EOF and truncates the
 * stream), so JARVIS connects through `adb forward` instead:
 *
 *   adb forward tcp:8791 tcp:8791
 *   adb shell -T CLASSPATH=/data/local/tmp/jarvis_voice.dex \
 *     app_process /system/bin com.jarvis.PcmServer --port 8791 --rate 24000
 *
 * The class prints "JARVIS_PCM_READY port=..." on stdout once it is listening.
 * One persistent connection carries every reply.
 *
 * Reading the socket and writing to AudioTrack run on SEPARATE threads joined
 * by a bounded queue. AudioTrack.write blocks whenever its small hardware
 * buffer is full; if the same thread also read the socket, it would stop
 * draining the TCP tunnel during playback and adb forward would abort the
 * connection under the resulting backpressure (observed as WSAECONNABORTED on
 * Windows). The reader thread therefore drains the socket continuously into a
 * queue the player consumes at its own pace.
 */
public class PcmServer {

    private static final int QUEUE_CHUNKS = 512;   // ~8 MB of headroom at 16 KB chunks
    private static final int READ_BUF = 16384;

    public static void main(String[] args) throws Exception {
        int port = 8791;
        int sampleRate = 24000;
        int channelMask = AudioFormat.CHANNEL_OUT_MONO;
        boolean sink = false;
        String bindAddr = "127.0.0.1";
        for (int i = 0; i < args.length - 1; i++) {
            if ("--port".equals(args[i])) {
                try { port = Integer.parseInt(args[i + 1]); } catch (Exception ignored) { }
            } else if ("--rate".equals(args[i])) {
                try { sampleRate = Integer.parseInt(args[i + 1]); } catch (Exception ignored) { }
            } else if ("--stereo".equals(args[i])) {
                channelMask = AudioFormat.CHANNEL_OUT_STEREO;
            } else if ("--sink".equals(args[i])) {
                sink = true;
            } else if ("--bind".equals(args[i]) && args[i + 1] != null) {
                bindAddr = args[i + 1];
            }
        }
        if (sampleRate <= 0) sampleRate = 24000;

        AudioTrack track = null;
        if (!sink) {
            track = buildTrack(sampleRate, channelMask);
            if (track == null || track.getState() != AudioTrack.STATE_INITIALIZED) {
                System.err.println("JARVIS_PCM_ERROR: AudioTrack not initialized");
                System.exit(3);
                return;
            }
            track.play();
            // Diagnostics: with stderr redirected to /dev/null in production this
            // is invisible; run with stderr visible to watch the playback head
            // advance and the underrun counter. A frozen head means the track is
            // disabled and the device is silent even though bytes are arriving.
            final AudioTrack mon = track;
            Thread monitor = new Thread(new Runnable() {
                @Override
                public void run() {
                    for (;;) {
                        try { Thread.sleep(1000); } catch (InterruptedException e) { return; }
                        try {
                            System.err.println("JARVIS_PCM_STATE state=" + mon.getState()
                                    + " play=" + mon.getPlayState()
                                    + " head=" + mon.getPlaybackHeadPosition()
                                    + " bufFrames=" + (mon.getBufferSizeInFrames()));
                        } catch (Throwable ignored) { }
                    }
                }
            }, "jarvis-pcm-monitor");
            monitor.setDaemon(true);
            monitor.start();
        }

        final ServerSocket server;
        try {
            server = new ServerSocket(port, 4, InetAddress.getByName(bindAddr));
        } catch (Throwable t) {
            System.err.println("JARVIS_PCM_ERROR: bind failed: " + t);
            System.exit(4);
            return;
        }
        System.out.println("JARVIS_PCM_READY port=" + port + " rate=" + sampleRate + " bind=" + bindAddr);
        System.out.flush();

        while (true) {
            Socket sock;
            try {
                sock = server.accept();
            } catch (Throwable t) {
                break;
            }
            try {
                playConnection(sock, track, sink);
            } catch (Throwable t) {
                System.err.println("JARVIS_PCM_CONN_ERROR " + t);
            }
        }

        try { if (track != null) track.stop(); } catch (Throwable ignored) { }
        if (track != null) track.release();
    }

    /** Reads one client's PCM on a reader thread; plays it on this thread. */
    private static void playConnection(final Socket sock, final AudioTrack track, final boolean sink) {
        System.err.println("JARVIS_PCM_CONN_START sink=" + sink);
        try { sock.setTcpNoDelay(true); } catch (Throwable ignored) { }

        final ArrayBlockingQueue<byte[]> queue = new ArrayBlockingQueue<>(QUEUE_CHUNKS);
        final boolean[] eof = {false};
        final long[] received = {0L};

        Thread reader = new Thread(new Runnable() {
            @Override
            public void run() {
                InputStream in;
                try {
                    in = sock.getInputStream();
                } catch (Throwable t) {
                    System.err.println("JARVIS_PCM_READER_OPEN_ERR " + t);
                    eof[0] = true;
                    return;
                }
                byte[] buf = new byte[READ_BUF];
                try {
                    int n;
                    while ((n = in.read(buf)) > 0) {
                        received[0] += n;
                        if (sink) {
                            continue;   // transport test: discard as fast as possible
                        }
                        byte[] copy = new byte[n];
                        System.arraycopy(buf, 0, copy, 0, n);
                        queue.put(copy);   // backpressure only when far ahead of playback
                    }
                } catch (Throwable t) {
                    System.err.println("JARVIS_PCM_READER_ERR " + t);
                } finally {
                    System.err.println("JARVIS_PCM_READER_DONE bytes=" + received[0]);
                    eof[0] = true;
                }
            }
        }, "jarvis-pcm-reader");
        reader.setDaemon(true);
        reader.start();

        try {
            while (!eof[0] || !queue.isEmpty()) {
                byte[] chunk = queue.poll(200, TimeUnit.MILLISECONDS);
                if (chunk == null) {
                    continue;
                }
                int off = 0;
                while (off < chunk.length) {
                    int w = (track == null) ? chunk.length : track.write(chunk, off, chunk.length - off);
                    if (w > 0) {
                        off += w;
                    } else if (w == 0) {
                        // Never spin: give the audio HAL a moment to drain.
                        try { Thread.sleep(5); } catch (InterruptedException ignored) { }
                    } else {
                        System.err.println("JARVIS_PCM_TRACK_ERROR " + w);
                        break;
                    }
                }
            }
        } catch (Throwable t) {
            System.err.println("JARVIS_PCM_PLAY_ERROR " + t);
        } finally {
            System.err.println("JARVIS_PCM_CONN_DONE bytes=" + received[0]);
            try { sock.close(); } catch (Throwable ignored) { }
        }
    }

    private static AudioTrack buildTrack(int sampleRate, int channelMask) {
        try {
            int minBuf = AudioTrack.getMinBufferSize(
                    sampleRate, channelMask, AudioFormat.ENCODING_PCM_16BIT);
            if (minBuf <= 0) minBuf = sampleRate;
            int bytesPerMs = (sampleRate * (channelMask == AudioFormat.CHANNEL_OUT_STEREO ? 4 : 2)) / 1000;
            // A generous ring: the phone's HAL underruns (and then disables) the
            // track whenever Wi-Fi jitter leaves its ~170 ms hardware buffer
            // empty - AudioFlinger showed "Active: no, Underruns: 24100" and the
            // device stayed silent. ~1.5 s of slack absorbs jitter and bursts
            // from the Live model without the PC having to run ahead of real
            // time. Playing still starts with the first chunk, so this adds no
            // startup latency, only headroom.
            int want = Math.max(minBuf, bytesPerMs * 1500);
            int ceiling = bytesPerMs * 4000;
            if (want > ceiling) want = ceiling;
            return new AudioTrack.Builder()
                    .setAudioAttributes(new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                            .build())
                    .setAudioFormat(new AudioFormat.Builder()
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(sampleRate)
                            .setChannelMask(channelMask)
                            .build())
                    .setBufferSizeInBytes(want)
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build();
        } catch (Throwable t) {
            return null;
        }
    }
}
