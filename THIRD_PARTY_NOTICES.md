# Third-party notices

Crestline is MIT-licensed (see [LICENSE](./LICENSE)) and derives from
[HiGHS](https://github.com/ERGO-Code/HiGHS) (Copyright (c) HiGHS developers,
MIT). It also contains code ported from, or links, the components below.

## Ported code

| Component | Origin | License |
|---|---|---|
| pdqsort (`src/mip/cuts/sort.rs`) | https://github.com/orlp/pdqsort, by Orson Peters (via HiGHS `extern/pdqsort`) | Zlib |
| LP file reader (`src/io/lp.rs`) | HiGHS `highs/io/filereaderlp`, Copyright (c) 2020 Michael Feldmeier | MIT |
| PDLP (`src/pdlp`) | cuPDLP-C, https://github.com/COPT-Public/cuPDLP-C (via HiGHS `highs/pdlp/cupdlp`) | see upstream |

pdqsort's licence (Zlib): Copyright (c) 2021 Orson Peters. This software is
provided 'as-is', without any express or implied warranty. In no event will
the authors be held liable for any damages arising from the use of this
software. Permission is granted to anyone to use this software for any
purpose, including commercial applications, and to alter it and redistribute
it freely, subject to the following restrictions: 1. The origin of this
software must not be misrepresented; you must not claim that you wrote the
original software. If you use this software in a product, an acknowledgment
in the product documentation would be appreciated but is not required.
2. Altered source versions must be plainly marked as such, and must not be
misrepresented as being the original software. 3. This notice may not be
removed or altered from any source distribution.

The command-line parser (`src/lp_data/options_cli.rs`) reproduces the
behaviour of [CLI11](https://github.com/CLIUtils/CLI11) (BSD-3-Clause), which
the HiGHS app uses; it contains no CLI11 code.

## Crates linked by `crest` (see Cargo.lock)

| Crate | Version | License |
|---|---|---|
| flate2 | 1.1.10 | MIT OR Apache-2.0 |
| miniz_oxide | 0.9.1 | MIT OR Zlib OR Apache-2.0 |
| adler2 | 2.0.1 | 0BSD OR MIT OR Apache-2.0 |
| simd-adler32 | 0.3.10 | MIT |
| crc32fast | 1.5.2 | MIT OR Apache-2.0 |
| cfg-if | 1.0.5 | MIT OR Apache-2.0 |
