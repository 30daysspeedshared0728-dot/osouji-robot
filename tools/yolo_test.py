#!/usr/bin/env python3
"""
yolo_test.py — ヘッドレスでYOLO物体検出を確認(1フレームだけ)
================================================================
GUI窓を出さず、検出ラベルを表示 ＋ 注釈画像を ~/yolo_test.jpg に保存する。
これは「これ何?」パイプライン(カメラ→YOLO→Gemmaが喋る)の最小部品でもある。

★Jetsonの罠を回避する既定＝CPU実行(device=cpu):
  - GPUで回すと Ollama(Gemma) と統合メモリを取り合って `NvMapMem ... error 12`(メモリ不足)。
  - さらに PyTorch が GPU情報をNVMLで問い合わせて落ちる既知バグ(`NVML_SUCCESS ... ASSERT`)がJetsonで出る。
  → だから既定はCPU。on-demandで1枚推論するだけなので速度も実用範囲。

  どうしてもGPUで試すなら:  Ollamaを止めて空けてから  DEVICE=0 で実行:
      sudo systemctl stop ollama
      DEVICE=0 python3 perception/yolo_test.py
  (それでもNVMLバグが出ることがある。まずCPUで"検出できる"を確認するのが吉)

使い方:
    python3 perception/yolo_test.py           # CPUで実行(推奨)
    CAM=1 python3 perception/yolo_test.py      # カメラ番号を変える
"""
import os
import cv2
from ultralytics import YOLO

CAM_INDEX = int(os.environ.get("CAM", "0"))     # USBカメラ番号。開けなければ 1,2 を試す。
DEVICE    = os.environ.get("DEVICE", "cpu")     # "cpu"=安定 / "0"=GPU(要Ollama停止＆NVMLバグ注意)
MODEL     = "yolov8n.pt"                         # 一番軽いモデル(初回だけ自動ダウンロード)


def main():
    print(f"YOLO読み込み中… device={DEVICE} (初回はモデルDLあり)")
    model = YOLO(MODEL)

    cap = cv2.VideoCapture(CAM_INDEX)
    if not cap.isOpened():
        print(f"[NG] カメラ({CAM_INDEX})が開けない。USB挿さってる? CAM=1 なども試して。")
        return
    ok, frame = cap.read()
    cap.release()
    if not ok:
        print("[NG] フレームが取れなかった。")
        return

    # ★窓は出さない(ヘッドレスOK)。検出結果だけ受け取る=ロボが本当に要るのは"ラベル"。
    res = model(frame, device=DEVICE, verbose=False)[0]
    labels = [model.names[int(c)] for c in res.boxes.cls]
    confs  = [float(s) for s in res.boxes.conf]
    print("=== 検出結果 ===")
    if labels:
        for name, cf in zip(labels, confs):
            print(f"  {name}  ({cf:.2f})")
    else:
        print("  (何も検出せず)")

    # 注釈画像を保存(あとでPCから scp で見れる)。
    out = os.path.join(os.path.expanduser("~"), "yolo_test.jpg")
    cv2.imwrite(out, res.plot())
    print(f"注釈画像を保存: {out}")
    print("  PCで見るなら:  scp jetson@192.168.0.15:~/yolo_test.jpg .")


if __name__ == "__main__":
    main()
