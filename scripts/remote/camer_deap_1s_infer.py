#!/usr/bin/env python3
"""Full one-second EEG+face inference; preserve original feature recipe and explicit clocks."""
from __future__ import annotations
import argparse
from io import BytesIO
import json
from pathlib import Path

import cv2
import numpy as np
import mediapipe as mp
from PIL import Image
import torch

from camer_eav_infer import load_model
from eav_eegnet_infer import load_source
from video_contract import probe_video


def prepare_eeg(signal, metadata, recipe):
    if (metadata["model_id"] != recipe["model_id"] or metadata["sample_rate_hz"] != 128 or
        metadata["channel_names"] != recipe["channel_names"] or metadata["unit"] != "dataset_native" or
        metadata["input_stage"] != "deap_preprocessed_trial"):
        raise ValueError("Expected declared DEAP preprocessed native-scale128Hz/32-electrode complete trial")
    if signal.ndim != 2 or signal.shape[0] != 32 or signal.shape[1] % 8064 or not np.isfinite(signal).all():
        raise ValueError("DEAP input requires complete finite63-second trials including3-second baseline")
    source = load_source("deap_original_delds", recipe["preprocessing_source"])
    features = []
    for start in range(0, signal.shape[1], 8064):
        values, _ = source.de_lds_pipeline(signal[:,start:start+8064],fs=128,
            band_dict=recipe["input_recipe"]["bands"],chunk_size=128,overlap=0,num_baseline=3,baseline_chunk_size=128,
            order=3,eps=1e-6,baseline_align=True,include_baseline_in_lds=False)
        features.append(values)
    all_inputs = np.concatenate(features)
    indices = []
    for row in metadata["windows"]:
        start, end = row["start_sample"], row["end_sample"]
        relative = start % 8064
        if relative < 384 or relative % 128 or end-start != 128 or end > signal.shape[1]:
            raise ValueError("Predict1-second stimulus windows; baseline cannot be classified")
        indices.append(start//8064*60+(relative-384)//128)
    return all_inputs[indices].astype(np.float32)


def prepare_video(video, windows, recipe):
    video_info = probe_video(video, require_cfr=True)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError("Cannot decode provided face video")
    fps = cap.get(cv2.CAP_PROP_FPS); count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if count != video_info["timing"]["n_frames"] or not np.isclose(fps,video_info["fps"],rtol=1e-9,atol=0):
        cap.release()
        raise ValueError("OpenCV native frame clock differs from verified video timing")
    duration = count/fps
    candidates = []
    for second in np.arange(0,duration,4):
        lo, hi = int(round(second*fps)),min(count,int(round((second+4)*fps)))
        candidates.extend(int(i) for i in np.linspace(lo,hi-1,16,dtype=int))
    candidates = sorted(set(candidates))
    detector = mp.solutions.face_detection.FaceDetection(model_selection=0,min_detection_confidence=.5)
    images, frame_times, boxes = [],[],[]
    decoded = {}
    try:
        for row in windows:
            t0,t1 = row["video_start_s"],row["video_end_s"]
            if t0 < 0 or t1 > duration + 1e-6 or t1 <= t0:
                raise ValueError(f"Provided video does not cover {t0}..{t1}s")
            available = [index for index in candidates if t0 <= index/fps < t1]
            if len(available) < 4:
                raise ValueError(f"Only {len(available)} real candidate frames in {t0}..{t1}s; DEAP requires four distinct frames")
            selected = [available[i] for i in np.linspace(0,len(available)-1,4,dtype=int)]
            window_images, window_boxes = [],[]
            for index in selected:
                if index not in decoded:
                    cap.set(cv2.CAP_PROP_POS_FRAMES,index)
                    ok,frame = cap.read()
                    if not ok:
                        raise ValueError(f"Cannot decode real video frame {index}")
                    detected = detector.process(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
                    if not detected.detections:
                        raise ValueError(f"No qualifying participant face at {index/fps:g}s")
                    if len(detected.detections) != 1:
                        raise ValueError(f"Multiple qualifying faces at {index/fps:g}s; DEAP requires one participant")
                    face = detected.detections[0].location_data.relative_bounding_box
                    width,height=frame.shape[1],frame.shape[0]
                    x,y,w,h=round(face.xmin*width),round(face.ymin*height),round(face.width*width),round(face.height*height)
                    px,py=round(.2*w),round(.2*h)
                    x0,y0,x1,y1=max(0,x-px),max(0,y-py),min(width,x+w+px),min(height,y+h+py)
                    resized = cv2.resize(frame[y0:y1,x0:x1],(224,224),interpolation=cv2.INTER_AREA)
                    ok,jpeg = cv2.imencode('.jpg',resized,[int(cv2.IMWRITE_JPEG_QUALITY),95])
                    if not ok:
                        raise ValueError("Frame encoding failed")
                    with Image.open(BytesIO(jpeg.tobytes())) as img:
                        rgb = np.asarray(img.convert('RGB').resize((224,224)),dtype=np.float32)/255.
                    decoded[index] = (rgb,[x0,y0,x1-x0,y1-y0])
                rgb,box = decoded[index]
                window_images.append(rgb);window_boxes.append(box)
            images.append(np.stack(window_images));boxes.append(window_boxes)
            frame_times.append([int(round(index/fps*1000)) for index in selected])
    finally:
        cap.release()
        detector.close()
    return np.stack(images),{"video_duration_s":duration,"source_fps":fps,"frames_per_window":4,
        "timing":video_info["timing"],"video_time_origin":"first decoded frame",
        "frame_times_ms":frame_times,"face_boxes_xywh":boxes,"crop":"MediaPipe short-range0, xywh rounded, separate20%padding",
        "crop_recipe_status":"recovered, byte-validated against original cache; historical source not found",
        "resize":"INTER_AREA224 then original PIL RGB/255","candidate_sampling":"16 indices per4-second block",
        "jpeg_quality":95,"zero_video_used":False}


def infer(request,response,video,recipe):
    with np.load(request,allow_pickle=False) as archive:
        signal=archive['signal'];metadata=json.loads(str(archive['metadata'].item()))
    eeg_inputs=prepare_eeg(signal,metadata,recipe)
    video_inputs,video_preparation=prepare_video(video,metadata['windows'],recipe)
    model=load_model(recipe).to('cuda:0')
    scores=[]
    with torch.inference_mode():
        for start in range(0,len(eeg_inputs),8):
            logits,_,_=model(torch.from_numpy(eeg_inputs[start:start+8]).to('cuda:0'),torch.from_numpy(video_inputs[start:start+8]).to('cuda:0'))
            scores.append(logits.cpu().numpy())
    logits=np.concatenate(scores)
    result={"model":{key:recipe[key] for key in ('model_id','model_version','task','weight_sha256','input_recipe')},
        "output":{"kind":"classification","labels":recipe['labels'],"score_kind":"logits","calibration":{"status":"unknown"}},
        "input_recipe":recipe['input_recipe'],
        "preparation":{"input_stage":"deap_preprocessed_trial","context_trial_s":63,"baseline_s":3,
                       "physical_voltage_unit":"unverified","tensor_shape":list(eeg_inputs.shape),"windows":metadata['windows']},
        "video_preparation":{**video_preparation,"sync_offset_s":metadata['sync_offset_s'],"tensor_shape":list(video_inputs.shape)},
        "runtime":{"torch":torch.__version__,"cuda":torch.version.cuda,"device":torch.cuda.get_device_name(0)}}
    np.savez_compressed(response,eeg_inputs=eeg_inputs,video_inputs=video_inputs,logits=logits,metadata=json.dumps(result,ensure_ascii=False))
    return {"status":"ok","model_id":recipe['model_id'],"prediction_count":len(logits),
            "predicted_labels":[recipe['labels'][i] for i in logits.argmax(axis=1)],"response":str(response)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--response',type=Path,required=True);parser.add_argument('--video',type=Path,required=True)
    parser.add_argument('--recipe',type=Path,default=Path(__file__).with_name('camer_deap_1s_recipe.json'))
    args=parser.parse_args();torch.set_num_threads(2);cv2.setNumThreads(2)
    print(json.dumps(infer(args.request,args.response,args.video,json.loads(args.recipe.read_text())),ensure_ascii=False))


if __name__=='__main__':
    main()
