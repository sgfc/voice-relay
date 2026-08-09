"""テスト用の偽 beatrice-host。実 VST/モデル無しで beatrice.py の契約を検証する。

- 起動時に stderr へ `READY 0` を出す (READY ハンドシェイク)。
- stdin から `uint32 count(LE) + float32*count` を読み、同じフレームを stdout へ返す
  (恒等変換)。EOF で終了。native/beatrice-host のプロトコルに準拠。
"""
import struct
import sys

_HDR = struct.Struct("<I")


def main() -> None:
    sys.stderr.write("READY 0\n")
    sys.stderr.flush()
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        h = stdin.read(4)
        if len(h) < 4:
            break
        (n,) = _HDR.unpack(h)
        payload = stdin.read(n * 4)
        if len(payload) < n * 4:
            break
        stdout.write(h)
        stdout.write(payload)
        stdout.flush()


if __name__ == "__main__":
    main()
