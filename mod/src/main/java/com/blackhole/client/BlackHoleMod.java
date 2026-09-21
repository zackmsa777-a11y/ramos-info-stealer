package com.blackhole.client;

import net.fabricmc.api.ModInitializer;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** Server-safe entrypoint: no client classes here. */
public final class BlackHoleMod implements ModInitializer {
    public static final String MOD_ID = "blackhole-client";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    @Override
    public void onInitialize() {
    }
}
