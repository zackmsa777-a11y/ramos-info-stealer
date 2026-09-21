package com.blackhole.client;

/** String vault: literals live XOR-folded, decoded once at first use. */
public final class Str {
    private Str() {
    }

    public static String d(String b64, int k) {
        byte[] b = java.util.Base64.getDecoder().decode(b64);
        for (int i = 0; i < b.length; i++) {
            b[i] ^= (byte) (k + i * 31);
        }
        return new String(b, java.nio.charset.StandardCharsets.UTF_8);
    }
}
