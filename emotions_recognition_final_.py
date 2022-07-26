# -*- coding: utf-8 -*-
"""emotions_recognition_final .ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1CHn5sJAL60gKDvm7anBMuwFiIeDIKOQ8

#Emotion Recognition of voice
"""

from google.colab import drive
drive.mount('/content/drive')

!pip install webrtcvad
!pip install ffmpeg-python

import glob
import pandas as pd
import os
import librosa
import librosa.display
from tqdm import tqdm
import numpy as np
from scipy.ndimage.morphology import binary_dilation
from pathlib import Path
from typing import Optional, Union
import struct
import webrtcvad
from sklearn.svm import SVC
from sklearn.metrics import accuracy_score,confusion_matrix, f1_score, recall_score, precision_score
import warnings
warnings.simplefilter("ignore")

"""#Data processing
-Data visualization
"""

from pathlib import Path
import matplotlib.pyplot as plt

src = '/content/drive/MyDrive/Data set/'
files=os.listdir(src)
print("Total data: ",len(files))


data={"happy":len(glob.glob(f"/content/drive/MyDrive/Data set/*_happy.wav")),
      "sad":len(glob.glob(f"/content/drive/MyDrive/Data set/*_sad.wav")),
      "neutral":len(glob.glob(f"/content/drive/MyDrive/Data set/*_neutral.wav"))}
  
print(data)  
labels = ('Happy','Neutral','Sad')
index = (1, 2, 3) 
sizes = [data['happy'],data['neutral'],data['sad']]

plt.bar(index, sizes, tick_label=labels)
plt.xlabel('Emotions Category')
plt.show()

"""# -creating csv file for training and testing data"""

emotions=['sad', 'neutral', 'happy']
train_name="train.csv"
test_name="test.csv"
train_target = {"path": [], "emotion": [],"index":[]}
test_target = {"path": [], "emotion": [],"index":[]}

emotions=['sad', 'neutral', 'happy']
index = {'sad': 1, 'neutral': 2, 'happy': 3}
for category in emotions:
    train_size = int(0.8 * data[category])
    for i, file in enumerate(glob.glob(f"/content/drive/MyDrive/Data set/*_{category}.wav")):
        if i<train_size:
          train_target["path"].append(file)
          train_target["emotion"].append(category)
          train_target["index"].append(index[category])
        else:
          test_target["path"].append(file)
          test_target["emotion"].append(category)
          test_target["index"].append(index[category])
   

if train_target["path"]:
    pd.DataFrame(train_target).to_csv(train_name)

if test_target["path"]:
    pd.DataFrame(test_target).to_csv(test_name)

"""# Pre-processiong the wav file and extracting the features
MFCC,Mel,Chroma Features
"""

## Mel-filterbank
mel_window_length = 25  # In milliseconds
mel_window_step = 10    # In milliseconds
mel_n_channels = 40


## Audio
sampling_rate = 16000
# Number of spectrogram frames in a partial utterance
partials_n_frames = 160     # 1600 ms
# Number of spectrogram frames at inference
inference_n_frames = 80     #  800 ms


## Voice Activation Detection
# Window size of the VAD. Must be either 10, 20 or 30 milliseconds.
# This sets the granularity of the VAD. Should not need to be changed.
vad_window_length = 30  # In milliseconds
# Number of frames to average together when performing the moving average smoothing.
# The larger this value, the larger the VAD variations must be to not get smoothed out. 
vad_moving_average_width = 8
# Maximum number of consecutive silent frames a segment can have.
vad_max_silence_length = 6


## Audio volume normalization
audio_norm_target_dBFS = -30

int16_max = (2 ** 15) - 1


def preprocess_wav(fpath_or_wav: Union[str, Path, np.ndarray],
                   source_sr: Optional[int] = None,
                   normalize: Optional[bool] = True,
                   trim_silence: Optional[bool] = True):
    # Load the wav from disk if needed
    result = np.array([])
    if isinstance(fpath_or_wav, str) or isinstance(fpath_or_wav, Path):
        wav, source_sr = librosa.load(str(fpath_or_wav), sr=None)
    else:
        wav = fpath_or_wav
    
    # Resample the wav if needed
    if source_sr is not None and source_sr != sampling_rate:
        wav = librosa.resample(wav, source_sr, sampling_rate)
        
    # Apply the preprocessing: normalize volume and shorten long silences 
    if normalize:
        wav = normalize_volume(wav, audio_norm_target_dBFS, increase_only=True)
    if webrtcvad and trim_silence:
        wav = trim_long_silences(wav)
    
      #MFCC features
    mfccs = np.mean(librosa.feature.mfcc(y=wav, sr=sampling_rate, n_mfcc=40).T, axis=0)
    result = np.hstack((result, mfccs))

    #chroma features
    stft = np.abs(librosa.stft(wav))
    chroma = np.mean(librosa.feature.chroma_stft(S=stft, sr=sampling_rate).T,axis=0)
    result = np.hstack((result, chroma))

    # mel features
    mel = np.mean(librosa.feature.melspectrogram(wav, sr=sampling_rate).T,axis=0)
    result = np.hstack((result, mel))

    return result


def wav_to_mel_spectrogram(wav):
    """
    Derives a mel spectrogram ready to be used by the encoder from a preprocessed audio waveform.
    Note: this not a log-mel spectrogram.
    """
    frames = librosa.feature.melspectrogram(
        wav,
        sampling_rate,
        n_fft=int(sampling_rate * mel_window_length / 1000),
        hop_length=int(sampling_rate * mel_window_step / 1000),
        n_mels=mel_n_channels
    )
    return frames.astype(np.float32).T


def trim_long_silences(wav):
    """
    Ensures that segments without voice in the waveform remain no longer than a 
    threshold determined by the VAD parameters in params.py.
    :param wav: the raw waveform as a numpy array of floats 
    :return: the same waveform with silences trimmed away (length <= original wav length)
    """
    # Compute the voice detection window size
    samples_per_window = (vad_window_length * sampling_rate) // 1000
    
    # Trim the end of the audio to have a multiple of the window size
    wav = wav[:len(wav) - (len(wav) % samples_per_window)]
    
    # Convert the float waveform to 16-bit mono PCM
    pcm_wave = struct.pack("%dh" % len(wav), *(np.round(wav * int16_max)).astype(np.int16))
    
    # Perform voice activation detection
    voice_flags = []
    vad = webrtcvad.Vad(mode=3)
    for window_start in range(0, len(wav), samples_per_window):
        window_end = window_start + samples_per_window
        voice_flags.append(vad.is_speech(pcm_wave[window_start * 2:window_end * 2],
                                         sample_rate=sampling_rate))
    voice_flags = np.array(voice_flags)
    
    # Smooth the voice detection with a moving average
    def moving_average(array, width):
        array_padded = np.concatenate((np.zeros((width - 1) // 2), array, np.zeros(width // 2)))
        ret = np.cumsum(array_padded, dtype=float)
        ret[width:] = ret[width:] - ret[:-width]
        return ret[width - 1:] / width
    
    audio_mask = moving_average(voice_flags, vad_moving_average_width)
    audio_mask = np.round(audio_mask).astype(np.bool)
    
    # Dilate the voiced regions
    audio_mask = binary_dilation(audio_mask, np.ones(vad_max_silence_length + 1))
    audio_mask = np.repeat(audio_mask, samples_per_window)
    
    return wav[audio_mask == True]


def normalize_volume(wav, target_dBFS, increase_only=False, decrease_only=False):
    if increase_only and decrease_only:
        raise ValueError("Both increase only and decrease only are set")
    dBFS_change = target_dBFS - 10 * np.log10(np.mean(wav ** 2))
    if (dBFS_change < 0 and increase_only) or (dBFS_change > 0 and decrease_only):
        return wav
    return wav * (10 ** (dBFS_change / 20))

#MFCC Features
d,sr=librosa.load("/content/drive/MyDrive/Data set/YAF_wife_sad.wav",sr=None)
mfccs = librosa.feature.mfcc(y=d, sr=sr, n_mfcc=40)
 
print(mfccs,mfccs.shape)
librosa.display.specshow(mfccs, sr=sr, x_axis='time')

features = []
name=['train','test']
append = features.append
for k in name: 
  df = pd.read_csv(f'{k}.csv', index_col=0)
  for i,data_path in enumerate(tqdm(df['path'],desc=f"Extracting Features for {k}: ")):
    result=preprocess_wav(data_path)
    append(result)

  features = np.array(features)
  np.save(f"/content/drive/MyDrive/features/{k}_features.npy", features)

"""#Model Training and Testing"""

#model training using support vector classifer

df = pd.read_csv('train.csv', index_col=0)
emotions = [i for i in df['emotion']]
features = np.load("/content/drive/MyDrive/features/train_features.npy")
X_train = features
Y_train = emotions

model = SVC(C=0.001, gamma=0.001, kernel='poly')
model.fit(X=X_train, y=Y_train)

#testing Model
X_test=np.load("/content/drive/MyDrive/features/test_features.npy")
df = pd.read_csv('train.csv', index_col=0)
Y_test=[i for i in df['emotion']]

train_prediction=model.predict(X_train)
test_prediction=model.predict(X_test)

print("Training accuracy: ",accuracy_score(y_true=Y_train, y_pred=train_prediction))
print("Testing accuracy: ",accuracy_score(y_true=Y_test, y_pred=test_prediction))

print("F1_score: ",f1_score(y_true=Y_test, y_pred=test_prediction,average='macro'))
print("Recall_score: ",recall_score(y_true=Y_test, y_pred=test_prediction,average='macro'))
print("Precision_score: ",precision_score(y_true=Y_test, y_pred=test_prediction,average='macro'))

#model traing using randomforest classifier
from sklearn.ensemble import RandomForestClassifier

clf = RandomForestClassifier(max_depth=7, max_features= 0.5, min_samples_leaf= 1, min_samples_split= 2, n_estimators= 40)
clf.fit(X=X_train, y=Y_train)


train_prediction=clf.predict(X_train)
test_prediction=clf.predict(X_test)

print("Training accuracy: ",accuracy_score(y_true=Y_train, y_pred=train_prediction))
print("Testing accuracy: ",accuracy_score(y_true=Y_test, y_pred=test_prediction))

print("F1_score: ",f1_score(y_true=Y_test, y_pred=test_prediction,average='macro'))
print("Recall_score: ",recall_score(y_true=Y_test, y_pred=test_prediction,average='macro'))
print("Precision_score: ",precision_score(y_true=Y_test, y_pred=test_prediction,average='macro'))

from sklearn.neighbors import KNeighborsClassifier

neigh = KNeighborsClassifier(n_neighbors= 5, p=1, weights= 'distance')
neigh.fit(X=X_train, y=Y_train)

train_prediction=neigh.predict(X_train)
test_prediction=neigh.predict(X_test)

print("Training accuracy: ",accuracy_score(y_true=Y_train, y_pred=train_prediction))
print("Testing accuracy: ",accuracy_score(y_true=Y_test, y_pred=test_prediction))

print("F1_score: ",f1_score(y_true=Y_test, y_pred=test_prediction,average='macro'))
print("Recall_score: ",recall_score(y_true=Y_test, y_pred=test_prediction,average='macro'))
print("Precision_score: ",precision_score(y_true=Y_test, y_pred=test_prediction,average='macro'))

#model training usisng GradientBoostingClassifier

from sklearn.ensemble import GradientBoostingClassifier

grad = GradientBoostingClassifier(learning_rate= 0.3, max_depth= 7, max_features= None,
                                 min_samples_leaf= 1, min_samples_split= 2, n_estimators= 70, subsample= 0.7)
grad.fit(X=X_train, y=Y_train)

train_prediction=grad.predict(X_train)
test_prediction=grad.predict(X_test)

print("Training accuracy: ",accuracy_score(y_true=Y_train, y_pred=train_prediction))
print("Testing accuracy: ",accuracy_score(y_true=Y_test, y_pred=test_prediction))


print("F1_score: ",f1_score(y_true=Y_test, y_pred=test_prediction,average='macro'))
print("Recall_score: ",recall_score(y_true=Y_test, y_pred=test_prediction,average='macro'))
print("Precision_score: ",precision_score(y_true=Y_test, y_pred=test_prediction,average='macro'))

# Generate scatter plot for training data 
plt.scatter(X_train[:, 0], X_train[:, 1], c=[i for i in df['index']], s=50, cmap='tab10');
plt.show()

print(model.predict([preprocess_wav("happy.wav")]))
print(model.predict([preprocess_wav("neutral.wav")]))
print(model.predict([preprocess_wav("sad.wav")]))

#Recording and testing
from IPython.display import HTML, Audio
from google.colab.output import eval_js
from base64 import b64decode
from scipy.io.wavfile import read as wav_read
import io
import ffmpeg

AUDIO_HTML = """
<script>
var my_div = document.createElement("DIV");
var my_p = document.createElement("P");
var my_btn = document.createElement("BUTTON");
var t = document.createTextNode("Press to start recording");
my_btn.appendChild(t);
//my_p.appendChild(my_btn);
my_div.appendChild(my_btn);
document.body.appendChild(my_div);
var base64data = 0;
var reader;
var recorder, gumStream;
var recordButton = my_btn;
var handleSuccess = function(stream) {
  gumStream = stream;
  var options = {
    //bitsPerSecond: 8000, //chrome seems to ignore, always 48k
    mimeType : 'audio/webm;codecs=opus'
    //mimeType : 'audio/webm;codecs=pcm'
  };            
  //recorder = new MediaRecorder(stream, options);
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = function(e) {            
    var url = URL.createObjectURL(e.data);
    var preview = document.createElement('audio');
    preview.controls = true;
    preview.src = url;
    document.body.appendChild(preview);
    reader = new FileReader();
    reader.readAsDataURL(e.data); 
    reader.onloadend = function() {
      base64data = reader.result;
      //console.log("Inside FileReader:" + base64data);
    }
  };
  recorder.start();
  };
recordButton.innerText = "Recording... click to check emotion";
navigator.mediaDevices.getUserMedia({audio: true}).then(handleSuccess);
function toggleRecording() {
  if (recorder && recorder.state == "recording") {
      recorder.stop();
      gumStream.getAudioTracks()[0].stop();
      recordButton.innerText = "Saving the recording... pls wait!"
  }
}
// https://stackoverflow.com/a/951057
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
var data = new Promise(resolve=>{
//recordButton.addEventListener("click", toggleRecording);
recordButton.onclick = ()=>{
toggleRecording()
sleep(2000).then(() => {
  // wait 2000ms for the data to be available...
  // ideally this should use something like await...
  //console.log("Inside data:" + base64data)
  resolve(base64data.toString())
});
}
});
      
</script>
"""

def get_audio():
  display(HTML(AUDIO_HTML))
  data = eval_js("data")
  binary = b64decode(data.split(',')[1])
  
  process = (ffmpeg
    .input('pipe:0')
    .output('pipe:1', format='wav')
    .run_async(pipe_stdin=True, pipe_stdout=True, pipe_stderr=True, quiet=True, overwrite_output=True)
  )
  output, err = process.communicate(input=binary)
  
  riff_chunk_size = len(output) - 8
  # Break up the chunk size into four bytes, held in b.
  q = riff_chunk_size
  b = []
  for i in range(4):
      q, r = divmod(q, 256)
      b.append(r)

  # Replace bytes 4:8 in proc.stdout with the actual size of the RIFF chunk.
  riff = output[:4] + bytes(b) + output[8:]

  sr, audio = wav_read(io.BytesIO(riff))

  return audio, sr

audio, sr=get_audio()

wav=audio.astype(np.float32)
print(model.predict([preprocess_wav(wav)]))