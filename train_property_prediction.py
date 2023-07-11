import os
import argparse
from tqdm import tqdm
import time
from datetime import datetime
import torch.optim as optim
import math
from torchvision import transforms
from models.slotattention import SlotAttentionAutoEncoder
import torch
from torchvision.utils import make_grid
import torch.nn as nn
from torch.utils.tensorboard import SummaryWriter
import torchvision
from models.model_prediction import Net
from torch.utils.data import Dataset,DataLoader
from data import Voc
from utils import transform

def visualize(img,slots):
    # `recon_combined` has shape: [num_channels,width, height].
    # `masks` has shape: [num_slots, width, height].
    # `recons` has shape: [num_slots, num_channels, width, height].
    #num_slot,__,__,__=recons.shape
    visz_list=[]
    num_slot=slots.shape[0]
    img=denormalize(img)
    visz_list.append(img)
    for i in range(num_slot):
        attention=slots[i].unsqueeze(0).repeat(3,1,1)
        visz_list.append(attention*img)
    visz_list=torch.stack(visz_list,0)
    visz_list=make_grid(visz_list.cpu(),nrow=len(visz_list))
    return visz_list



def get_available_devices():
    sys_gpu = torch.cuda.device_count()

    device = torch.device('cuda:0' if sys_gpu > 0 else 'cpu')
    available_gpus = list(range(sys_gpu))
    return device, available_gpus


parser = argparse.ArgumentParser()

parser.add_argument
parser.add_argument('--model_dir', default='./tmp/model10.ckpt', type=str, help='where to save models')
parser.add_argument('--seed', default=0, type=int, help='random seed')
parser.add_argument('--batch_size', default=64, type=int)
parser.add_argument('--image_size', default=224, type=int)
parser.add_argument('--num_slots', default=6, type=int, help='Number of slots in Slot Attention.')
parser.add_argument('--num_iterations', default=3, type=int, help='Number of attention iterations.')
parser.add_argument('--hid_dim', default=256, type=int, help='hidden dimension size')
parser.add_argument('--learning_rate', default=0.0004, type=float)
parser.add_argument('--warmup_steps', default=10000, type=int, help='Number of warmup steps for the learning rate.')
parser.add_argument('--decay_rate', default=0.5, type=float, help='Rate for the learning rate decay.')
parser.add_argument('--train_steps', default=6000, type=int, help='Number of steps for the learning rate decay.')
parser.add_argument('--num_workers', default=4, type=int, help='number of workers for loading data')
parser.add_argument('--num_epochs', default=1000, type=int, help='number of workers for loading data')
parser.add_argument('--log_path', default='./log', type=str, help='number of workers for loading data')
parser.add_argument('--different_lr', default=False, type=bool, help='number of workers for loading data')

args = parser.parse_args()

train_trans = transform.Compose([transform.HorizontalFilp(),
                           transform.Crop(base_size=256, crop_height=224, crop_width=224, type='random'),
                           transform.GaussianBlur(),
                           transform.ToTensor(),
                           transform.Normalize(std=[0.229, 0.224, 0.225],
                                               mean=[0.485, 0.456, 0.406])
                           ])

val_trans = transform.Compose([transform.HorizontalFilp(),
                           transform.Crop(base_size=256, crop_height=224, crop_width=224, type='center'),
                           transform.ToTensor(),
                           transform.Normalize(std=[0.229, 0.224, 0.225],
                                               mean=[0.485, 0.456, 0.406])
                           ])
denormalize = transform.DeNormalize(std=[0, 0, 0],
                                               mean=[0, 0, 0])
split = 'train_aug'
data_root = "D:\\datasets\\VOC\\VOCdevkit\\VOC2012"
data_list = "D:\\datasets\\VOC\\VOCdevkit\\VOC2012\\ImageSets\\Segmentation\\train_aug.txt"

train_split = 'train_aug'
val_split='val_aug'
data_root = "D:\\datasets\\VOC\\VOCdevkit\\VOC2012"

train_set = Voc.VOC_Dataset(split=train_split, data_root=data_root, transform=train_trans)
val_set = Voc.VOC_Dataset(split=val_split, data_root=data_root, transform=val_trans)

col_map = train_set.palette
train_dataloader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True, num_workers=0)
val_dataloader = DataLoader(val_set, batch_size=args.batch_size, shuffle=True, num_workers=0)

device, available_gpus=get_available_devices()
model=Net(image_size=args.image_size,num_slot=args.num_slots,num_iteration=args.num_iterations,
          slot_dim=args.hid_dim).to(device)

model.load_state_dict(torch.load("checkpoints/oroc_pascal_voc_best_model.pth"), strict=False)
#print(msg)
if len(available_gpus)>0:
    model = torch.nn.DataParallel(model, device_ids=available_gpus)
train_epoch_size = len(train_dataloader)
val_epoch_size=len(val_dataloader)
#print(train_epoch_size)


if args.different_lr:
    if isinstance(model, torch.nn.DataParallel):
        trainable_params = [{'params': filter(lambda p: p.requires_grad, model.module.get_decoder_params())},
                            {'params': filter(lambda p: p.requires_grad, model.module.get_slotattention_params()),
                             'lr': args.learning_rate / 10}]
    else:
        trainable_params = [{'params': filter(lambda p: p.requires_grad, model.get_decoder_params())},
                            {'params': filter(lambda p: p.requires_grad, model.get_slotattention_params()),
                             'lr': args.learning_rate / 10}]
else:
    trainable_params = filter(lambda p: p.requires_grad, model.parameters())
    # for name, param in model.named_parameters():
    #     if param.requires_grad:
    #         print(name)
optimizer = optim.Adam(trainable_params, lr=args.learning_rate)
log_dir = os.path.join(args.log_path)
writer = SummaryWriter(log_dir)
best_val_loss=math.inf
start = time.time()
i=0
while(i<args.train_steps):
    model.train()
    total_loss = 0
    for batch_idx,sample in enumerate(train_dataloader,0):
        i += 1

        learning_rate = args.learning_rate * (args.decay_rate ** (
                i / args.train_steps))

        optimizer.param_groups[0]['lr'] = learning_rate

        image=sample['img']
        class_label=sample['class_label']
        fg_channel=sample['fg_channel']
        image = image.to(device)
        class_label=class_label.to(device)
        fg_channel=fg_channel.to(device)
        optimizer.zero_grad()

        slots,mlp_pred,matching_loss = model(image,class_label,fg_channel)
        total_loss += matching_loss.item()

        if batch_idx<train_epoch_size-1:
            del slots,mlp_pred,


        matching_loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()

        if i > args.warmup_steps and i % train_epoch_size == 0:
            writer.add_scalar('train_loss', total_loss, i)

    total_loss /= train_epoch_size

    print("Train | Step: {}, Loss: {}".format(i, total_loss))
    with torch.no_grad():
        model.eval()
        val_loss= 0.
        for sample in tqdm(val_dataloader):
            image = sample['img']
            image = image.to(device)
            class_label = sample['class_label'].to(device)
            fg_channel = sample['fg_channel'].to(device)
            slots,mlp_pred,matching_loss= model(image,class_label,fg_channel)
            val_loss += matching_loss.item()

        val_loss /= val_epoch_size
        writer.add_scalar('Val/matching_loss', total_loss, i + 1)
        print('====> Val | Step: {:3} \t Loss = {:F}'.format(i + 1, val_loss))
        if val_loss < best_val_loss:
            best_val_loss=val_loss
            torch.save(model.state_dict(), 'checkpoints/oroc_pascal_voc_best_model.pth')


torch.save(model.state_dict(), "checkpoints/oroc_pascalvoc_final.pth")


