import random
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
from torch.autograd import Variable
import numpy as np
import cv2
import torchvision.transforms as TF
from PIL import Image
from model.utils.config import cfg
from model.rpn.rpn import _RPN

from model.roi_layers import ROIAlign, ROIPool

# from model.roi_pooling.modules.roi_pool import _RoIPooling
# from model.roi_align.modules.roi_align import RoIAlignAvg

from model.rpn.proposal_target_layer_cascade import _ProposalTargetLayer
import time
import pdb
from model.utils.net_utils import _smooth_l1_loss, _crop_pool_layer, _affine_grid_gen, _affine_theta

# Below 2 networks are for attention

class Network1(nn.Module):
    def __init__(self,c,h,w):
        super(Network1, self).__init__()
        self.channels = c
        self.maxPool = nn.MaxPool2d(kernel_size=(h,w))
        self.avgPool = nn.AvgPool2d(kernel_size=(h,w))
        self.fc1 = nn.Linear(in_features=c,out_features=c//8)
        self.fc2 = nn.Linear(in_features=c//8,out_features=c//8)
        self.fc3 = nn.Linear(in_features=c//8,out_features=c)

    
    def forwardAvg(self,x):
        x = F.relu(self.avgPool(x))
        x = x.view(-1,self.channels)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = F.relu(self.fc3(x))
        return x


    def forwardMax(self,x):
        x = F.relu(self.maxPool(x))
        x = x.view(-1,self.channels)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = F.relu(self.fc3(x))
        return x
    
    def forward(self,x):
        x_avg = self.forwardAvg(x)
        x_max = self.forwardMax(x)
        return torch.sigmoid(x_avg+x_max)



class Network2(nn.Module):
    def __init__(self):
        super(Network2, self).__init__()
        self.conv1 = nn.Conv2d(in_channels=2,out_channels=1,kernel_size=(1,1))
        self.conv2 = nn.Conv2d(in_channels=1,out_channels=1,kernel_size=(1,1))

    
    def forward(self,x):
        x1,_ = torch.max(x,dim=1)
        x1 = F.relu(x1)
        x1.unsqueeze_(1)
        x2 = F.relu(torch.mean(x,dim=1))
        x2.unsqueeze_(1)
        x = torch.cat([x1,x2],dim=1)
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        return torch.sigmoid(x)  


def apply_attention_fmap(feat,c,h,w):
    model_1 = Network1(c,h,w).cuda()
    f_ch = model_1(feat)
    f_ch = f_ch.view(c,1,1)
    f_ch = torch.mul(f_ch,feat)
    # print(f_ch.shape)

    model_2 = Network2().cuda()
    f_sp = model_2(f_ch)
    f_sp = F.relu(torch.mul(f_sp,f_ch))
    # print(f_sp.shape)
    f = F.relu(f_sp + f_ch)
    # print(f.shape)
    return f


class _fasterRCNN(nn.Module):
    """ faster RCNN """
    def __init__(self, classes, class_agnostic):
        super(_fasterRCNN, self).__init__()
        self.classes = classes
        self.n_classes = len(classes)
        self.class_agnostic = class_agnostic
        # loss
        self.RCNN_loss_cls = 0
        self.RCNN_loss_bbox = 0

        # define rpn
        self.RCNN_rpn = _RPN(self.dout_base_model)
        self.RCNN_proposal_target = _ProposalTargetLayer(self.n_classes)

        # self.RCNN_roi_pool = _RoIPooling(cfg.POOLING_SIZE, cfg.POOLING_SIZE, 1.0/16.0)
        # self.RCNN_roi_align = RoIAlignAvg(cfg.POOLING_SIZE, cfg.POOLING_SIZE, 1.0/16.0)

        self.RCNN_roi_pool = ROIPool((cfg.POOLING_SIZE, cfg.POOLING_SIZE), 1.0/16.0)
        self.RCNN_roi_align = ROIAlign((cfg.POOLING_SIZE, cfg.POOLING_SIZE), 1.0/16.0, 0)

    def forward(self, im_data_1, im_data_2, im_info, gt_boxes, num_boxes):

        # im_data_1 is rgb and im_data_2 is thermal, but both have 3 channels

        batch_size = im_data_1.size(0)
        
        im_info = im_info.data
        gt_boxes = gt_boxes.data
        num_boxes = num_boxes.data
        
        # feed image data to base models to obtain base feature map
        # im_data_1,2 have shapes [1,3,512,640]                                                                                                       
        rgb_feat = self.RCNN_base_1(im_data_1) # feat 1,2 have shapes [1, 1024, 32, 40]
        thermal_feat = self.RCNN_base_2(im_data_2)

        # apply attention

        # feat_1 = apply_attention_fmap(feat_1,feat_1.size(1),feat_1.size(2),feat_1.size(3))
        # feat_2 = apply_attention_fmap(feat_2,feat_2.size(1),feat_2.size(2),feat_2.size(3))

        
        # Mutan Fusion
        
        # NUM_LAYERS = 3
        # conv_layer_1 = nn.Conv2d(1024,2048,kernel_size=(3,3),padding=1).cuda() # 1024 is input channels, 2048 is output channels
        # conv_layer_2 = nn.ModuleList([
        #     nn.Conv2d(2048, 2048,kernel_size=(3,3),padding=1)
        #     for i in range(NUM_LAYERS)]).cuda()

        # feat1 = conv_layer_1(feat_1)
        # feat2 = conv_layer_1(feat_2)
        # # feat1 = nn.Dropout(0.25)(feat1)
        # # feat2 = nn.Dropout(0.25)(feat2)

        # x_mm = []
        
        # for i in range(NUM_LAYERS):
        #     x1 = conv_layer_2[i](feat1)
        #     x1 = nn.Tanh()(x1)
            
        #     x2 = conv_layer_2[i](feat2)
        #     x2 = nn.Tanh()(x2)
            
        #     x_mm.append(torch.mul(x1,x2))

        # x_mm = torch.stack(x_mm,dim=1)
        # batch_size = x_mm.size(0)
        # # nc,w,h = x_mm.shape[2],x_mm.shape[3],x_mm.shape[4]
        # combined_feat_mutan = torch.sum(x_mm,dim=1)
        # print(combined_feat.shape)

        
        # MLB Fusion

        # conv_layer_1 = nn.Conv2d(1024,2048,kernel_size=(3,3),padding=1).cuda()
        # conv_layer_2 = nn.Conv2d(1024,2048,kernel_size=(3,3),padding=1).cuda()

        # feat_1 = conv_layer_1(feat_1)
        # # feat_1 = nn.Tanh()(feat_1)
        
        # feat_2 = conv_layer_2(feat_2)
        # # feat_2 = nn.Tanh()(feat_2)
        # combined_feat = torch.mul(feat_1,feat_2)
        
        
        # Different fusion scheme (first one suggested by Himanshu) (works)

        # w = feat_1.size(2)
        # h = feat_1.size(3)
        # feat_1 = feat_1.view(feat_1.size(0),feat_1.size(1),feat_1.size(2)*feat_1.size(3))
        # feat_2 = feat_2.view(feat_1.size(0),feat_1.size(1),feat_2.size(2)*feat_2.size(3))
        # combined_feat_h = torch.cat([feat_1,feat_2],dim=1)
        # combined_feat_h = combined_feat_h.view(combined_feat_h.size(0),combined_feat_h.size(1),w,h)

        # combined_feat = combined_feat_h


        # combined_feat = combined_feat_h + combined_feat_mutan 
        
        # Below line uses original fusion scheme
        
        # combined_feat = torch.cat([feat_1, feat_2], dim=1) # combined feat has shape [1, 2048, 32, 40]
        
        # base_feat = self.RCNN_base_3(combined_feat)

        # Late fusion

        # feed base feature map tp RPN to obtain rois


        # base_feat = thermal_feat

        rois, rpn_loss_cls, rpn_loss_bbox = self.RCNN_rpn(thermal_feat, im_info, gt_boxes, num_boxes)
        rois_rgb, _, _ = self.RCNN_rpn(rgb_feat, im_info, gt_boxes, num_boxes)

        # if it is training phrase, then use ground trubut bboxes for refining
        if self.training:
            roi_data = self.RCNN_proposal_target(rois, gt_boxes, num_boxes)
            rois, rois_label, rois_target, rois_inside_ws, rois_outside_ws = roi_data

            roi_data_rgb = self.RCNN_proposal_target(rois_rgb, gt_boxes, num_boxes)
            rois_rgb, rois_label_rgb, rois_target_rgb, rois_inside_ws_rgb, rois_outside_ws_rgb = roi_data_rgb 

        else:
            rois_label = None
            rois_target = None
            rois_inside_ws = None
            rois_outside_ws = None

            rois_label_rgb = None
            rois_target_rgb = None
            rois_inside_ws_rgb = None
            rois_outside_ws_rgb = None

            rpn_loss_cls = 0
            rpn_loss_bbox = 0

        rois_orig_thermal = rois
        rois_orig_rgb = rois_rgb
        
        rois = Variable(rois)
        rois_rgb = Variable(rois_rgb)
        # do roi pooling based on predicted rois

        if cfg.POOLING_MODE == 'align':
            pooled_feat_thermal = self.RCNN_roi_align(thermal_feat, rois.view(-1, 5))
            pooled_feat_rgb = self.RCNN_roi_align(rgb_feat, rois_rgb.view(-1, 5))
        elif cfg.POOLING_MODE == 'pool':
            pooled_feat_thermal = self.RCNN_roi_pool(thermal_feat, rois.view(-1,5))
            pooled_feat_rgb = self.RCNN_roi_pool(rgb_feat, rois_rgb.view(-1, 5))

        # print(pooled_feat_thermal.shape)
        # print(pooled_feat_rgb.shape)
        pooled_feat = torch.cat([pooled_feat_thermal ,pooled_feat_rgb], dim=0)
        # print('combined',pooled_feat.shape)
        # base_feat = self.RCNN_base_3(combined_feat)

        # feed pooled features to top model
        pooled_feat = self._head_to_tail(pooled_feat)

        # compute bbox offset
        bbox_pred = self.RCNN_bbox_pred(pooled_feat)
        if self.training:
            # rois = torch.cat([rois,rois_rgb],dim=1)
            rois_label = torch.cat([rois_label,rois_label_rgb],dim=0)
            rois_target = torch.cat([rois_target,rois_target_rgb],dim=0)
            rois_inside_ws = torch.cat([rois_inside_ws,rois_inside_ws_rgb],dim=0)
            rois_outside_ws = torch.cat([rois_outside_ws,rois_outside_ws_rgb],dim=0)

            rois_label = Variable(rois_label.view(-1).long())
            rois_target = Variable(rois_target.view(-1, rois_target.size(2)))
            rois_inside_ws = Variable(rois_inside_ws.view(-1, rois_inside_ws.size(2)))
            rois_outside_ws = Variable(rois_outside_ws.view(-1, rois_outside_ws.size(2)))

        else:
            rois = torch.cat([rois,rois_rgb],dim=1)

        if self.training and not self.class_agnostic:
            # select the corresponding columns according to roi labels
            bbox_pred_view = bbox_pred.view(bbox_pred.size(0), int(bbox_pred.size(1) / 4), 4)
            bbox_pred_select = torch.gather(bbox_pred_view, 1, rois_label.view(rois_label.size(0), 1, 1).expand(rois_label.size(0), 1, 4))
            bbox_pred = bbox_pred_select.squeeze(1)

        # compute object classification probability
        cls_score = self.RCNN_cls_score(pooled_feat)
        cls_prob = F.softmax(cls_score, 1)

        RCNN_loss_cls = 0
        RCNN_loss_bbox = 0

        if self.training:
            # classification loss
            RCNN_loss_cls = F.cross_entropy(cls_score, rois_label)

            # bounding box regression L1 loss
            RCNN_loss_bbox = _smooth_l1_loss(bbox_pred, rois_target, rois_inside_ws, rois_outside_ws)


        cls_prob = cls_prob.view(batch_size, rois.size(1), -1)
        bbox_pred = bbox_pred.view(batch_size, rois.size(1), -1)

        return rois, cls_prob, bbox_pred, rpn_loss_cls, rpn_loss_bbox, RCNN_loss_cls, RCNN_loss_bbox, rois_label

    def _init_weights(self):
        def normal_init(m, mean, stddev, truncated=False):
            """
            weight initalizer: truncated normal and random normal.
            """
            # x is a parameter
            if truncated:
                m.weight.data.normal_().fmod_(2).mul_(stddev).add_(mean) # not a perfect approximation
            else:
                m.weight.data.normal_(mean, stddev)
                m.bias.data.zero_()

        normal_init(self.RCNN_rpn.RPN_Conv, 0, 0.01, cfg.TRAIN.TRUNCATED)
        normal_init(self.RCNN_rpn.RPN_cls_score, 0, 0.01, cfg.TRAIN.TRUNCATED)
        normal_init(self.RCNN_rpn.RPN_bbox_pred, 0, 0.01, cfg.TRAIN.TRUNCATED)
        normal_init(self.RCNN_cls_score, 0, 0.01, cfg.TRAIN.TRUNCATED)
        normal_init(self.RCNN_bbox_pred, 0, 0.001, cfg.TRAIN.TRUNCATED)

    def create_architecture(self):
        self._init_modules()
        self._init_weights()
