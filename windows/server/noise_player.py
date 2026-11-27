"""Filho minúsculo do Ruído: toca o WAV em laço (winsound, sem biblioteca extra) e some se o servidor morrer.
Uso: noise_player.py <arquivo.wav> <pid do servidor>"""
import sys
import time


def main():
    path, ppid = sys.argv[1], int(sys.argv[2])
    import psutil
    import winsound
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_LOOP | winsound.SND_ASYNC)
    while psutil.pid_exists(ppid):
        time.sleep(2)


if __name__ == "__main__":
    main()
