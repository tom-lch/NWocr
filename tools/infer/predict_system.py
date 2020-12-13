# Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import sys

__dir__ = os.path.dirname(os.path.abspath(__file__))
sys.path.append(__dir__)
sys.path.append(os.path.abspath(os.path.join(__dir__, '../..')))

import tools.infer.utility as utility
from ppocr.utils.utility import initial_logger

logger = initial_logger()
import cv2
import tools.infer.predict_det as predict_det   # 导入文本预测位置框预测模型
import tools.infer.predict_rec as predict_rec   # 导入文本识别模型
import tools.infer.predict_cls as predict_cls   # 导入文本分类模型
import copy
import numpy as np
import math
import time
from ppocr.utils.utility import get_image_file_list, check_and_read_gif  # 分别是获取图片list、检查图片和读取gif格式的图片
from PIL import Image
from tools.infer.utility import draw_ocr            # 绘制ocr
from tools.infer.utility import draw_ocr_box_txt    # 绘制ocr框

# 文本检测系统
class TextSystem(object):
    def __init__(self, args):
        self.text_detector = predict_det.TextDetector(args) # 文本检测器
        self.text_recognizer = predict_rec.TextRecognizer(args)  # 文本识别器
        self.use_angle_cls = args.use_angle_cls
        if self.use_angle_cls:                               # 是否使用分类
            self.text_classifier = predict_cls.TextClassifier(args)   # 使用分类器
    # 拿到旋转的图片
    def get_rotate_crop_image(self, img, points):
        '''
        img_height, img_width = img.shape[0:2]  h,w,c
        left = int(np.min(points[:, 0]))
        right = int(np.max(points[:, 0]))
        top = int(np.min(points[:, 1]))
        bottom = int(np.max(points[:, 1]))
        img_crop = img[top:bottom, left:right, :].copy()
        points[:, 0] = points[:, 0] - left
        points[:, 1] = points[:, 1] - top
        '''
        img_crop_width = int(
            max(
                np.linalg.norm(points[0] - points[1]),
                np.linalg.norm(points[2] - points[3])))
        img_crop_height = int(
            max(
                np.linalg.norm(points[0] - points[3]),
                np.linalg.norm(points[1] - points[2])))
        pts_std = np.float32([[0, 0], [img_crop_width, 0],
                              [img_crop_width, img_crop_height],
                              [0, img_crop_height]])
        M = cv2.getPerspectiveTransform(points, pts_std)
        dst_img = cv2.warpPerspective(
            img,
            M, (img_crop_width, img_crop_height),
            borderMode=cv2.BORDER_REPLICATE,
            flags=cv2.INTER_CUBIC)
        dst_img_height, dst_img_width = dst_img.shape[0:2]
        if dst_img_height * 1.0 / dst_img_width >= 1.5:
            dst_img = np.rot90(dst_img)
        return dst_img
    # 打印保存图片
    def print_draw_crop_rec_res(self, img_crop_list, rec_res):
        bbox_num = len(img_crop_list)
        for bno in range(bbox_num):
            cv2.imwrite("./output/img_crop_%d.jpg" % bno, img_crop_list[bno])
            print(bno, rec_res[bno])
    # class实例化的时候会调用__call__方法
    def __call__(self, img):
        ori_im = img.copy()
        # 使用文本检测器检测文本，返回检测到文本框的数量和时间
        dt_boxes, elapse = self.text_detector(img)
        print("dt_boxes num : {}, elapse : {}".format(len(dt_boxes), elapse))
        if dt_boxes is None:
            # 如果没有检测到文本框就返回
            return None, None
        img_crop_list = []
        # 从上到下、从左到右对文本框进行排序
        dt_boxes = sorted_boxes(dt_boxes)

        for bno in range(len(dt_boxes)):
            tmp_box = copy.deepcopy(dt_boxes[bno]) # 使用深拷贝复制框
            img_crop = self.get_rotate_crop_image(ori_im, tmp_box) # 根据图片和框计算图片局部信息
            img_crop_list.append(img_crop)
        if self.use_angle_cls:   # 使用分类模型对局部文本进行分类
            img_crop_list, angle_list, elapse = self.text_classifier(
                img_crop_list)
            print("cls num  : {}, elapse : {}".format(
                len(img_crop_list), elapse))
        rec_res, elapse = self.text_recognizer(img_crop_list)  # 进行文本识别
        print("rec_res num  : {}, elapse : {}".format(len(rec_res), elapse))
        # self.print_draw_crop_rec_res(img_crop_list, rec_res)
        return dt_boxes, rec_res


def sorted_boxes(dt_boxes):
    """
    Sort text boxes in order from top to bottom, left to right
    args:
        dt_boxes(array):detected text boxes with shape [4, 2]
    return:
        sorted boxes(array) with shape [4, 2]
    """
    num_boxes = dt_boxes.shape[0]
    sorted_boxes = sorted(dt_boxes, key=lambda x: (x[0][1], x[0][0]))
    _boxes = list(sorted_boxes)

    for i in range(num_boxes - 1):
        if abs(_boxes[i + 1][0][1] - _boxes[i][0][1]) < 10 and \
                (_boxes[i + 1][0][0] < _boxes[i][0][0]):
            tmp = _boxes[i]
            _boxes[i] = _boxes[i + 1]
            _boxes[i + 1] = tmp
    return _boxes


def main(args):
    image_file_list = get_image_file_list(args.image_dir) # 获取文件夹下的图片列表
    text_sys = TextSystem(args)   # 实例化文本检测模型
    is_visualize = True       # 可视化 -> 使用cv2绘图
    font_path = args.vis_font_path  # 文字路径
    for image_file in image_file_list: # 对每一帧进行检测
        img, flag = check_and_read_gif(image_file)
        if not flag:
            img = cv2.imread(image_file)
        if img is None:
            logger.info("error in loading image:{}".format(image_file))
            continue
        starttime = time.time() # 开始时间
        dt_boxes, rec_res = text_sys(img)  # 检测 框、文件信息
        elapse = time.time() - starttime
        print("Predict time of %s: %.3fs" % (image_file, elapse))

        drop_score = 0.5
        dt_num = len(dt_boxes)
        for dno in range(dt_num): # 分析每一个文本框
            text, score = rec_res[dno] # 分析文本和置信度
            if score >= drop_score:
                text_str = "%s, %.3f" % (text, score)
                print(text_str)

        if is_visualize:
            image = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            boxes = dt_boxes
            txts = [rec_res[i][0] for i in range(len(rec_res))]
            scores = [rec_res[i][1] for i in range(len(rec_res))]

            draw_img = draw_ocr_box_txt(
                image,
                boxes,
                txts,
                scores,
                drop_score=drop_score,
                font_path=font_path)
            draw_img_save = "./inference_results/"
            if not os.path.exists(draw_img_save):
                os.makedirs(draw_img_save)
            cv2.imwrite(
                os.path.join(draw_img_save, os.path.basename(image_file)),
                draw_img[:, :, ::-1])
            print("The visualized image saved in {}".format(
                os.path.join(draw_img_save, os.path.basename(image_file))))


if __name__ == "__main__":
    main(utility.parse_args())
