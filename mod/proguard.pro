# BlackHole obfuscation: mangle everything except Fabric entrypoints.
-dontshrink
-dontoptimize
-allowaccessmodification
-repackageclasses 'bh'
-overloadaggressively
-keepattributes Signature,InnerClasses,EnclosingMethod
-dontwarn **

# Fabric entrypoints must keep names (referenced by fabric.mod.json).
-keep public class com.blackhole.client.BlackHoleMod {
    public static final java.lang.String MOD_ID;
    public static final org.slf4j.Logger LOGGER;
    public void onInitialize();
}
-keep public class com.blackhole.client.BlackHoleClient {
    public void onInitializeClient();
}
