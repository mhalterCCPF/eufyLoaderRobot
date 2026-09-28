Import("env")

# Wokwi (see wokwi.toml) expects a single merged flash image, but a plain
# `pio run` only produces the separate bootloader/partitions/app bins.
# Merge them after every build so firmware-merged.bin never goes stale.


def merge_bin(source, target, env):
    build_dir = env.subst("$BUILD_DIR")
    env.Execute(
        " ".join(
            [
                '"$PYTHONEXE"',
                '"$UPLOADER"',
                "--chip esp32s3 merge_bin",
                "--flash_mode qio --flash_freq 80m --flash_size 8MB",
                f"-o {build_dir}/firmware-merged.bin",
                f"0x0 {build_dir}/bootloader.bin",
                f"0x8000 {build_dir}/partitions.bin",
                f"0x10000 {build_dir}/firmware.bin",
            ]
        )
    )


env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", merge_bin)
