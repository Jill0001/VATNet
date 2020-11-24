import os

os.environ['CUDA_VISIBLE_DEVICES'] = ""

import torch
from torch.utils.data import Dataset, DataLoader
from video_audio_dataset import VideoAudioDataset
import torch.nn as nn
from torch.autograd import Variable
import torch.optim as optim
import numpy as np
from torch.nn import Parameter
from torchvision import datasets, transforms
from pytorch_i3d import videotransforms
from torch import autograd
import argparse
from pytorch_i3d.pytorch_i3d import InceptionI3d
from myrnn import RNN
from pytorch_i3d.extract_features_training import ExtractVideoFeature
import time
import random
from tqdm import tqdm

test_transforms = transforms.Compose([videotransforms.CenterCrop(224)])


def load_data(data_root, json_name):
    dataset = VideoAudioDataset(data_root, os.path.join(data_root, json_name), transform=test_transforms)
    dataloader = DataLoader(dataset, batch_size=1, num_workers=4, shuffle=True, drop_last=True)

    return dataloader


parser = argparse.ArgumentParser()

parser.add_argument("--data_root", help="data main root")
parser.add_argument("--save_model_path", help="path to save models (.pth)")
# parser.add_argument("--topics_m_path", help="origin topics matrix path (.npy)")

args = parser.parse_args()

data_root = args.data_root
saved_model_path = args.save_model_path

if not os.path.exists(saved_model_path):
    os.makedirs(saved_model_path)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

LOAD_MODEL = False

if LOAD_MODEL:
    saved_model_list = os.listdir(saved_model_path)

    model = torch.load(saved_model_path)
    # model.load_state_dict(model['state_dict'])
    print('loading checkpoint!')
else:
    model = RNN(1024, 845, 1024, 3).to(device)

optimizer = optim.SGD(model.parameters(), lr=0.0001, momentum=0.9)

train_dataloader = load_data(data_root, 'train_label_pos.json')
val_dataloader = load_data(data_root,'val_label_pos.json')

i3d = InceptionI3d(400, in_channels=3)
i3d.replace_logits(157)
i3d.load_state_dict(torch.load('./pytorch_i3d/models/rgb_charades.pt'))
i3d.to(device)
i3d.train(False)  # Set model to evaluate mode

criterion_c = nn.CrossEntropyLoss()
criterion_t = nn.CrossEntropyLoss()
criterion = nn.CrossEntropyLoss()


def consistency_aug(audio_npy):
    #shape: 1 50 845
    audio_npy_chucks = []
    for i in range(5):
        audio_npy_chucks.append(audio_npy[:,i*10:(i+1)*10,:])
    random.shuffle(audio_npy_chucks)
    tensor_npy = audio_npy_chucks[0]
    for j in audio_npy_chucks[1:]:
        tensor_npy = torch.cat((tensor_npy,j),axis=1)
    return tensor_npy


wr = open('./score.txt', 'w')
time_now = time.time()


def extract_v_feature(input_v):
    with torch.no_grad():
        features = i3d.extract_features(input_v)
    features = features.squeeze(0).permute(1, 2, 3, 0)

    zero4concate = torch.zeros((50 - features.shape[0], 1, 1, 1024)).to(device)
    features = torch.cat((features, zero4concate), axis=0)
    features = features.squeeze()
    features = features.unsqueeze(0)
    return features


def eval_net(net, loader, device):
    """Evaluation without the densecrf with the dice coefficient"""
    net.eval()
    n_val = len(loader)  # the number of batch
    c_right =0
    with tqdm(total=n_val, desc='Validation round', unit='batch', leave=False) as pbar:
        for batch in loader:
            input_a = i['np_A'].to(device)
            input_v = i['np_V'].to(device)

            input_t = i['text_data'].float().to(device)
            feature = extract_v_feature(input_v)
            with torch.no_grad():
                c_out, mf_out, t_out = net(feature, input_a, input_t)
            # c_out, mf_out, t_out = saved_model(input_v, input_a, input_t)

            va_label = i['va_label']
            text_label = i['text_label']

            # print(c_out)
            if c_out[0] > c_out[1]:
                c_result = False
            else:
                c_result = True

            # if t_out[0][0] > t_out[0][1]:
            #     t_result = False
            # else:
            #     t_result = True

            # print(t_result)

            if va_label == c_result:
                c_right = c_right + 1
            # if text_label == t_result:
            #     t_right = t_right + 1

            # if va_label == c_result and text_label == t_result:
            #     all_right = all_right + 1

            pbar.update()
    net.train()
    return c_right/n_val


for epoch in range(2000):  # loop over the dataset multiple times

    running_loss = 0.0
    for idx, i in enumerate(train_dataloader):

        input_a = i['np_A'].to(device)
        input_v = i['np_V'].to(device)
        input_t = i['text_data'].float().to(device)

        va_label = i['va_label'].long().to(device)
        text_label = i['text_label'].long().to(device)

        is_reverse = np.random.rand(1)
        if is_reverse> 0.5 and va_label:
            input_a = consistency_aug(input_a)
            va_label = va_label-1
        features = extract_v_feature(input_v)

        optimizer.zero_grad()
        c_out, mf_out, t_out = model(features, input_a, input_t)
        c_out = c_out.unsqueeze(0)

        # all_out = (c_out * t_out)
        # all_label = (va_label * text_label)
        # all_loss = criterion(all_out, all_label)

        # print(va_label)
        c_loss = criterion_c(c_out, va_label)
        loss = c_loss
        # t_loss = criterion_t(t_out, text_label)

        # mf_loss = mf_out
        # loss = all_loss + mf_loss

        loss.backward()
        optimizer.step()



        # print statistics

        running_loss += loss.item()
        if idx % 10 == 9:  # print every * mini-batches
            cost_time = time.time() - time_now
            time_now = time.time()
            print('[%d, %5d] loss: %.3f, cost_time: %.3f' %
                  (epoch + 1, idx + 1, running_loss / 10, cost_time))
            wr.write('%d, %5d loss: %.3f\n' %
                     (epoch + 1, idx + 1, running_loss / 10))
            wr.flush()
            running_loss = 0.0
            eval_result = eval_net(model, val_dataloader, device)
            print(eval_result)
    if epoch % 20 == 0:
        # pass
        print('Saving Net...')
        torch.save(model, os.path.join(saved_model_path, 'epoch' + str(epoch) + '.pth'))
