# @title  { display-mode: "form" }
# @markdown # 3. Train the Model (patched for Zade)
# @markdown Paste this whole file over cell 3's code in the openWakeWord Colab notebook.
# @markdown Needs cells 1 and 2 to have run in this session. When finished, the .onnx downloads automatically.

import os
import pathlib
import shutil
import sys

import yaml

# Wake word: use the spelling that sounded right in cell 1.
target_word = "hey_zayd"  # @param {type:"string"}
number_of_examples = 5000  # @param {type:"slider", min:100, max:50000, step:50}
number_of_training_steps = 20000  # @param {type:"slider", min:0, max:50000, step:100}
false_activation_penalty = 1500  # @param {type:"slider", min:100, max:5000, step:50}

# Fix: DeepPhonemizer's model link is dead (downloads an HTML page -> "invalid load key '<'").
# It only invents similar-sounding phrases, so skip it and list them by hand below.
data_py = pathlib.Path("openwakeword/openwakeword/data.py")
source = data_py.read_text()
if "_orig_generate_adversarial_texts" not in source:
    source = source.replace(
        "def generate_adversarial_texts(",
        "def generate_adversarial_texts(*args, **kwargs):\n"
        "    return []\n"
        "\n"
        "def _orig_generate_adversarial_texts(",
        1,
    )
    data_py.write_text(source)

# Back up progress to Google Drive: free Colab can take the machine back at any time and wipe
# the disk. Features (the slow part) are saved after augmenting; a rerun restores them and
# skips straight to the ~10 minute training step.
from google.colab import drive

drive.mount("/content/drive")
backup_dir = os.path.join("/content/drive/MyDrive/zade_wakeword", target_word)
os.makedirs(backup_dir, exist_ok=True)
work_dir = os.path.join("my_custom_model", target_word.replace(" ", "_"))
have_backup = os.path.exists(os.path.join(backup_dir, "positive_features_train.npy"))

shutil.rmtree("my_custom_model", ignore_errors=True)
if have_backup:
    shutil.copytree(backup_dir, work_dir)
    print("RESTORED features from Google Drive, skipping clip generation and augmentation")

config = yaml.load(open("openwakeword/examples/custom_model.yml", "r").read(), yaml.Loader)
config["target_phrase"] = [target_word]
config["model_name"] = target_word.replace(" ", "_")
config["n_samples"] = number_of_examples
config["n_samples_val"] = max(500, number_of_examples // 10)
config["steps"] = number_of_training_steps
config["target_accuracy"] = 0.5
config["target_recall"] = 0.25
config["output_dir"] = "./my_custom_model"
config["max_negative_weight"] = false_activation_penalty
config["background_paths"] = ["./audioset_16k", "./fma"]
config["false_positive_validation_data_path"] = "validation_set_features.npy"
config["feature_data_files"] = {"ACAV100M_sample": "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"}

# Phrases that sound close to the wake word: the model learns NOT to wake on these.
config["custom_negative_phrases"] = [
    "hey jade", "hey made", "hey wade", "hey shade", "hey fade", "hey kate", "hey nate",
    "hey dave", "hey zack", "hey sadie", "hey say", "hey they", "hey there", "hey day",
    "hey stay", "hey play", "hey maid", "hey trade", "hey zane", "hey zayn", "hey jay",
    "hey its late", "hey wait", "hey great", "zade", "hey", "hey you", "okay",
    "hey google", "hey siri", "alexa", "hey jarvis", "made it", "pay day", "hazy days",
]

config_file = "my_model.yaml"
with open(config_file, "w") as f:
    yaml.dump(config, f)

train_script = os.path.join("openwakeword", "openwakeword", "train.py")
steps = ["train_model"] if have_backup else ["generate_clips", "augment_clips", "train_model"]
for step in steps:
    command = " ".join([sys.executable, train_script, "--training_config", config_file, "--" + step])
    print("RUNNING:", command)
    get_ipython().system(command)
    if step == "augment_clips":
        for name in os.listdir(work_dir):
            if name.endswith(".npy"):
                shutil.copy(os.path.join(work_dir, name), backup_dir)
        print("BACKED UP features to Google Drive:", backup_dir)

# Zade only needs the .onnx file, so the tflite conversion is skipped.
model_name = config["model_name"]
onnx_path = os.path.join("my_custom_model", model_name + ".onnx")
if os.path.exists(onnx_path):
    from google.colab import files

    shutil.copy(onnx_path, backup_dir)
    files.download(onnx_path)
    print("DONE:", onnx_path, "(also saved in Google Drive:", backup_dir + ")")
else:
    print("FAILED: no .onnx produced. Scroll up to the FIRST 'Traceback' and send it.")
