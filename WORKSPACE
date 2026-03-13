load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

http_archive(
    name = "aspect_gcc_toolchain",
    sha256 = "3341394b1376fb96a87ac3ca01c582f7f18e7dc5e16e8cf40880a31dd7ac0e1e",
    strip_prefix = "gcc-toolchain-0.4.2",
    urls = [
        "https://github.com/aspect-build/gcc-toolchain/archive/refs/tags/0.4.2.tar.gz",
    ],
)

load("@aspect_gcc_toolchain//toolchain:repositories.bzl", "gcc_toolchain_dependencies")

gcc_toolchain_dependencies()

load("@aspect_gcc_toolchain//toolchain:defs.bzl", "gcc_register_toolchain", "ARCHS")

gcc_register_toolchain(
    name = "gcc_toolchain_x86_64",
    target_arch = ARCHS.x86_64,
)

gcc_register_toolchain(
    name = "gcc_toolchain_armv7",
    target_arch = ARCHS.armv7,
)

gcc_register_toolchain(
    name = "gcc_toolchain_aarch64",
    target_arch = ARCHS.aarch64,
)


local_repository(
    name = "secda_tools",
    path = "/home/jude/Workspace/SECDA/secda_tools",
)


# load("@bazel_tools//tools/build_defs/repo:git.bzl", "git_repository")
# git_repository(
#     name = "secda_tools",
#     remote = "https://github.com/judeharis/secda_tools.git",
#     commit = "6026859096b9b6c4859dd71c94656b30124b2e5c",
# )   


new_local_repository(
    name = "xrt",
    path = "/mnt/Crucial/Xilinx2024/SDK/sysroots/cortexa72-cortexa53-xilinx-linux/usr/include/xrt/",
    build_file_content = """
package(
    default_visibility = [
        "//visibility:public",
    ],
)

cc_library(
    name = "headers",
    srcs = glob(["**/*.h"]),
)
""",
)