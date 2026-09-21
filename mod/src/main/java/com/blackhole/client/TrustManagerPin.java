package com.blackhole.client;

import java.security.MessageDigest;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;

import javax.net.ssl.X509TrustManager;

/** Trusts exactly one certificate: the pinned fingerprint. Nothing else. */
public final class TrustManagerPin implements X509TrustManager {
    // Operator: SHA-256 of panel.crt (DER). Baked at private build time.
    static final String PIN_HEX = Str.d("THlesJL12mcVIWYY+Y7jnysJb06t1rbFJ1YsDOyerdM4HHMF5Mfw0DkRWT1MrYi8yHpTZRT2gbDHJE18VO6Yqw==", 41);

    @Override
    public void checkServerTrusted(X509Certificate[] chain, String authType) throws CertificateException {
        if (chain == null || chain.length == 0) throw new CertificateException("empty chain");
        try {
            MessageDigest sha = MessageDigest.getInstance("SHA-256");
            byte[] fp = sha.digest(chain[0].getEncoded());
            StringBuilder hex = new StringBuilder(fp.length * 2);
            for (byte b : fp) {
                hex.append(Character.forDigit((b >> 4) & 0xF, 16));
                hex.append(Character.forDigit(b & 0xF, 16));
            }
            if (!hex.toString().equalsIgnoreCase(PIN_HEX)) throw new CertificateException("pin mismatch");
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new CertificateException(e);
        }
    }

    @Override public void checkClientTrusted(X509Certificate[] chain, String authType) {}
    @Override public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
}
