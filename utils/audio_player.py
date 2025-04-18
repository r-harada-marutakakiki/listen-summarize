"""
QtMultimedia を使用した音声プレーヤーユーティリティ (ffmpegによる一時変換)
"""

import os
import time
import subprocess
import tempfile
import traceback
from PyQt5.QtCore import QObject, pyqtSignal, QUrl, QTimer
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent # QAudioOutputは不要

# スクリプトの場所に基づいて ffmpeg.exe のパスを決定
# utils/audio_player.py から見て、プロジェクトルート (main.pyがある階層) の
# 中にある Faster-Whisper-XXL ディレクトリの中にあると仮定
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir) # utilsの一つ上の階層
ffmpeg_dir_name = "Faster-Whisper-XXL"
ffmpeg_exe_name = "ffmpeg.exe"
ffmpeg_path = os.path.join(project_root, ffmpeg_dir_name, ffmpeg_exe_name)
print(f"使用するffmpegのパス: {ffmpeg_path}")

class AudioPlayer(QObject):
    """QMediaPlayerを使用して音声を再生するためのクラス (ffmpegで一時WAV変換)"""

    position_changed = pyqtSignal(int)  # 現在の再生位置（ミリ秒）
    duration_changed = pyqtSignal(int)  # 音声の長さ（ミリ秒）
    state_changed = pyqtSignal(int)    # 再生状態の変更（0=停止, 1=再生中, 2=一時停止）
    error_occurred = pyqtSignal(str)   # エラー発生時

    # QMediaPlayerの状態定数 (互換性のため)
    class State:
        StoppedState = QMediaPlayer.StoppedState
        PlayingState = QMediaPlayer.PlayingState
        PausedState = QMediaPlayer.PausedState

    # DirectShowエラーコード (参考)
    DIRECTSHOW_ERROR_CODE = "0x80040266"

    def __init__(self):
        super().__init__()
        self.player = QMediaPlayer()
        # self.audio_output = QAudioOutput() # 不要
        # self.player.setAudioOutput(self.audio_output) # 不要

        self.original_file = "" # 元ファイルのパス
        self.temp_wav_file = None # 一時WAVファイルのパス
        self.end_position = None
        self.last_known_good_position = 0

        # エラーリトライ用 (ffmpeg変換で不要になる可能性が高いが念のため残す)
        self.retry_count = 0
        self.max_retries = 3

        # QMediaPlayerのシグナル接続
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.stateChanged.connect(self._on_playback_state_changed)
        self.player.error.connect(self._on_error)

        # セグメント終了タイマー
        self.segment_end_timer = QTimer()
        self.segment_end_timer.setInterval(50)
        self.segment_end_timer.timeout.connect(self._check_segment_end)

    def _cleanup_temp_file(self):
        """一時ファイルを削除する"""
        if self.temp_wav_file and os.path.exists(self.temp_wav_file):
            try:
                os.remove(self.temp_wav_file)
                print(f"一時ファイルを削除しました: {self.temp_wav_file}")
                self.temp_wav_file = None
            except Exception as e:
                print(f"一時ファイルの削除に失敗しました: {e}")

    def load_file(self, file_path):
        """音声ファイルをffmpegで一時WAVに変換して読み込む (バンドル版ffmpegを使用)"""
        if not os.path.exists(file_path):
            self.error_occurred.emit(f"ファイルが存在しません: {file_path}")
            return False

        self.player.stop()
        self._cleanup_temp_file()
        self.original_file = os.path.abspath(file_path)
        self.retry_count = 0

        try:
            # ffmpeg実行ファイルの存在確認
            if not os.path.exists(ffmpeg_path):
                error_msg = f"バンドルされたffmpegが見つかりません: {ffmpeg_path}"
                print(error_msg)
                self.error_occurred.emit(error_msg)
                return False

            # 一時WAVファイルのパスを生成
            temp_dir = tempfile.gettempdir()
            temp_wav_name = f"audio_player_temp_{int(time.time())}.wav"
            self.temp_wav_file = os.path.join(temp_dir, temp_wav_name)
            print(f"一時WAVファイルパス: {self.temp_wav_file}")

            # ffmpegコマンドの構築 (バンドルされたffmpegのパスを使用)
            ffmpeg_cmd = [ffmpeg_path, '-i', self.original_file, '-y', self.temp_wav_file]
            print(f"ffmpegコマンド実行: {' '.join(ffmpeg_cmd)}")

            # ffmpegを実行
            process = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

            if process.returncode != 0:
                error_msg = f"ffmpegでの変換に失敗しました (Code: {process.returncode})\n実行パス: {ffmpeg_path}\nエラー出力:\n{process.stderr}"
                print(error_msg)
                self.error_occurred.emit(error_msg)
                self._cleanup_temp_file()
                return False

            print("ffmpegでの変換成功")

            # 変換された一時WAVファイルをQMediaPlayerにセット
            url = QUrl.fromLocalFile(self.temp_wav_file)
            content = QMediaContent(url)
            self.player.setMedia(content)
            print(f"一時WAVファイルを準備完了: {self.temp_wav_file}")
            return True

        except FileNotFoundError:
             # このエラーは↑のos.path.existsで捕捉されるはずだが念のため
             error_msg = f"ffmpegが見つかりません: {ffmpeg_path}"
             print(error_msg)
             self.error_occurred.emit(error_msg)
             self._cleanup_temp_file()
             return False
        except Exception as e:
            error_msg = f"ファイル読み込み/変換エラー: {str(e)}"
            print(error_msg)
            traceback.print_exc()
            self.error_occurred.emit(error_msg)
            self._cleanup_temp_file()
            return False

    def play(self, start_position_ms=None):
        """再生を開始 (指定された位置から)"""
        # 一時ファイルが準備されているか確認
        if self.player.mediaStatus() == QMediaPlayer.NoMedia or not self.temp_wav_file:
             msg = "再生エラー: メディアが設定されていないか、一時ファイルがありません。"
             print(msg)
             self.error_occurred.emit(msg)
             return False

        if start_position_ms is not None:
             print(f"再生開始位置を設定: {start_position_ms}ms")
             self.set_position(start_position_ms)
             self.last_known_good_position = start_position_ms

        print("再生開始")
        try:
            self.player.play()
            if self.end_position is not None:
                 self.segment_end_timer.start()
            return True
        except Exception as e:
            self.error_occurred.emit(f"再生開始エラー: {str(e)}")
            return False

    def pause(self):
        """再生を一時停止/再開"""
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            print("一時停止")
            self.player.pause()
            self.segment_end_timer.stop() # 一時停止中はタイマーも停止
        elif self.player.playbackState() == QMediaPlayer.PausedState:
            print("再生再開")
            self.player.play()
            if self.end_position is not None:
                self.segment_end_timer.start() # 再開時にタイマーも再開

    def stop(self):
        """再生を停止"""
        print("停止")
        self.player.stop()
        self.segment_end_timer.stop()
        self.end_position = None
        self.last_known_good_position = 0
        # 停止時に一時ファイルを削除するかどうかは検討事項
        # すぐに再利用する可能性があるなら削除しない方が効率的かもしれない
        # self._cleanup_temp_file() # ここで消すと再生の度に変換が必要になる

    def set_position(self, position_ms):
        """再生位置を設定 (ミリ秒単位)"""
        if self.player.mediaStatus() >= QMediaPlayer.LoadedMedia:
             print(f"位置を設定: {position_ms}ms")
             self.player.setPosition(max(0, position_ms))
        else:
             print(f"警告: メディア未ロードのため位置設定スキップ: {position_ms}ms")

    def get_position(self):
        """現在の再生位置を取得 (ミリ秒単位)"""
        return self.player.position()

    def get_duration(self):
        """音声の長さを取得 (ミリ秒単位)"""
        return self.player.duration()

    def get_state(self):
        """現在の再生状態を取得"""
        return self.player.playbackState()

    def set_end_position(self, position_ms):
        """セグメント再生の終了位置を設定 (ミリ秒単位)"""
        if position_ms is not None and position_ms > 0:
             print(f"セグメント終了位置を設定: {position_ms}ms")
             self.end_position = max(0, position_ms)
        else:
             self.end_position = None
             self.segment_end_timer.stop()

    def _check_segment_end(self):
        """タイマーでセグメント終了位置をチェック"""
        if self.end_position is not None and self.player.position() >= self.end_position:
             current_pos = self.player.position()
             print(f"セグメント終了位置に到達: {current_pos}ms >= {self.end_position}ms")
             # self.stop() # stop() だと状態がリセットされすぎるので pause() に変更
             self.player.pause() # 停止ではなく一時停止にする
             self.segment_end_timer.stop() # タイマーを止める
             self.end_position = None # 終了位置をリセット
             # 必要であれば、位置をend_positionぴったりに調整する
             # self.player.setPosition(self.end_position)
             print("セグメント終了、一時停止しました")

    def _on_position_changed(self, position):
        """QMediaPlayerからの再生位置変更シグナル"""
        self.position_changed.emit(position)
        if self.player.error() == QMediaPlayer.NoError:
             self.last_known_good_position = position

    def _on_duration_changed(self, duration):
        """QMediaPlayerからの期間変更シグナル"""
        print(f"Duration検出: {duration}ms")
        self.duration_changed.emit(duration)

    def _on_media_status_changed(self, status):
        """QMediaPlayerのメディアステータス変更シグナル"""
        print(f"メディアステータス変更: {status}")
        if status == QMediaPlayer.LoadedMedia:
             print("メディア読み込み完了 (一時WAV)")
        elif status == QMediaPlayer.InvalidMedia:
             # ffmpegで変換しているので、これが起きる可能性は低いが...
             error_msg = f"無効なメディアファイルです (一時WAV: {self.temp_wav_file})"
             self.error_occurred.emit(error_msg)
             print(f"エラー: {error_msg}")
        elif status == QMediaPlayer.EndOfMedia:
             print("メディア再生終了")
             self.stop()

    def _on_playback_state_changed(self, state):
         """QMediaPlayerの再生状態変更シグナル"""
         print(f"再生状態変更: {state}")
         self.state_changed.emit(state)
         if state == QMediaPlayer.StoppedState:
              self.segment_end_timer.stop()
              self.end_position = None

    def _on_error(self, error):
        """QMediaPlayerのエラーシグナル"""
        error_string = self.player.errorString()
        error_code_hex = f"{error:#0{10}x}"
        print(f"QMediaPlayerエラー発生: Code={error}, Hex={error_code_hex}, Message={error_string}, File={self.temp_wav_file}")

        # ffmpeg変換によりDirectShowエラーは減るはずだが、リトライロジックは残す
        if error_code_hex == self.DIRECTSHOW_ERROR_CODE:
            if self.retry_count < self.max_retries:
                self.retry_count += 1
                print(f"DirectShowエラー ({error_code_hex}) を検出。リトライします ({self.retry_count}/{self.max_retries})。")
                QTimer.singleShot(500 * self.retry_count, self._retry_playback)
            else:
                error_msg = f"DirectShowエラー ({error_code_hex}) が最大リトライ回数 ({self.max_retries}) を超えました。再生を停止します。"
                print(error_msg)
                self.error_occurred.emit(error_msg)
                self.stop()
        else:
            self.error_occurred.emit(f"再生エラー: {error_string} (Code: {error_code_hex})")

    def _retry_playback(self):
         """DirectShowエラーからの再生リトライ処理"""
         print(f"再生リトライ実行: 位置を {self.last_known_good_position}ms に戻して再生")
         self.player.stop()
         QTimer.singleShot(100, lambda: self._perform_retry_play())

    def _perform_retry_play(self):
         # リトライ時は一時ファイルが存在するか確認
         if self.temp_wav_file and os.path.exists(self.temp_wav_file):
             self.set_position(self.last_known_good_position)
             self.player.play()
             print("リトライ再生開始試行")
         else:
             print("リトライ再生失敗: 一時ファイルが見つかりません")
             self.error_occurred.emit("リトライ再生に失敗しました (一時ファイル喪失)")

    def cleanup(self):
         """リソース解放 (一時ファイルの削除)"""
         print("AudioPlayer クリーンアップ")
         self.player.stop()
         self._cleanup_temp_file() # アプリ終了時に一時ファイルを削除 