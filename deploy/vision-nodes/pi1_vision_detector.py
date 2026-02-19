#!/usr/bin/env python3
"""
Sentient Core - Vision Detection Service (Pi1 Node)

Raspberry Pi 5 + Hailo-8 accelerator (26 TOPS)
Camera: OV5647 on CSI cam1 (640x640 main only)
Model: YOLOv8s via Hailo TAPPAS HEF

Features:
  - YOLOv8s inference on Hailo-8 NPU
  - MJPEG stream with bounding box overlays on port 8090
  - MQTT detection publishing to Jetson perception
  - Time-based MQTT throttle (500ms min interval)

Deploy:
    scp pi1_vision_detector.py mjpeg_server.py pi1@192.168.1.219:/opt/sentient-node/
    # Then on Pi1:
    sudo systemctl restart sentient-vision
"""

import json
import time
import signal
import logging
import threading
from datetime import datetime

import cv2
import numpy as np
import paho.mqtt.client as mqtt

# Hailo imports (only on Pi hardware)
try:
    from picamera2 import Picamera2
    from hailo_platform import HEF, VDevice, ConfigureParams, \
        InputVStreamParams, OutputVStreamParams, FormatType, \
        InferVStreams, HailoStreamInterface
    HAS_HAILO = True
except ImportError:
    HAS_HAILO = False

from mjpeg_server import MJPEGServer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CAMERA_SIZE = (640, 640)
MODEL_PATH = "/usr/share/hailo-models/yolov8s_h8.hef"

MQTT_BROKER = "192.168.1.159"
MQTT_PORT = 1883
MQTT_USER = "sentient"
MQTT_PASS = "sentient1312"
CAMERA_ID = "pi1"
LOCATION = "portable"

CONFIDENCE_THRESHOLD = 0.3
MQTT_THROTTLE_S = 0.5
MJPEG_PORT = 8090
MJPEG_FPS = 10

# COCO class names
COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
    "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush"
]

# Colors for bounding boxes (BGR)
BOX_COLORS = [
    (0, 255, 0), (255, 0, 0), (0, 0, 255), (255, 255, 0),
    (255, 0, 255), (0, 255, 255), (128, 255, 0), (255, 128, 0),
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/opt/sentient-node/vision.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('vision')

# ---------------------------------------------------------------------------
# Detection pipeline
# ---------------------------------------------------------------------------

class VisionDetector:
    def __init__(self):
        self.running = True
        self.mqtt_client = None
        self.mjpeg_server = None
        self.last_publish_time = 0
        self.last_classes = set()
        self.frame_count = 0
        self.detection_count = 0

        # Signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, sig, frame):
        logger.info(f"Signal {sig} received, shutting down...")
        self.running = False

    def connect_mqtt(self):
        self.mqtt_client = mqtt.Client(client_id=f"vision-{CAMERA_ID}")
        self.mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
        self.mqtt_client.on_connect = lambda c, u, f, rc: logger.info(f"MQTT connected (rc={rc})")
        self.mqtt_client.on_disconnect = lambda c, u, rc: logger.warning(f"MQTT disconnected (rc={rc})")
        self.mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        self.mqtt_client.loop_start()
        logger.info(f"MQTT connecting to {MQTT_BROKER}:{MQTT_PORT}")

    def start_mjpeg(self):
        self.mjpeg_server = MJPEGServer(port=MJPEG_PORT, target_fps=MJPEG_FPS)
        self.mjpeg_server.start()

    def parse_hailo_detections(self, raw_detections, frame_w, frame_h):
        """Parse Hailo NMS output: list of 80 arrays, each (N, 5) = [y_min, x_min, y_max, x_max, conf]"""
        results = []
        for class_id, class_dets in enumerate(raw_detections):
            if not isinstance(class_dets, np.ndarray) or class_dets.size == 0:
                continue
            if class_dets.ndim == 1:
                class_dets = class_dets.reshape(1, -1)
            for det in class_dets:
                if len(det) < 5:
                    continue
                conf = float(det[4])
                if conf < CONFIDENCE_THRESHOLD:
                    continue
                y_min, x_min, y_max, x_max = det[0:4]
                results.append({
                    "class": COCO_CLASSES[class_id] if class_id < len(COCO_CLASSES) else f"class_{class_id}",
                    "class_id": class_id,
                    "confidence": round(conf, 3),
                    "bbox": {
                        "x_min": round(float(x_min) * frame_w),
                        "y_min": round(float(y_min) * frame_h),
                        "x_max": round(float(x_max) * frame_w),
                        "y_max": round(float(y_max) * frame_h),
                    }
                })
        return results

    def draw_detections(self, frame, detections):
        """Draw bounding boxes and labels on frame."""
        for det in detections:
            bbox = det["bbox"]
            x1, y1 = int(bbox["x_min"]), int(bbox["y_min"])
            x2, y2 = int(bbox["x_max"]), int(bbox["y_max"])
            color = BOX_COLORS[det["class_id"] % len(BOX_COLORS)]
            label = f'{det["class"]} {det["confidence"]:.0%}'

            # Draw box
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            # Draw label background
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
            cv2.putText(frame, label, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

        # Draw info overlay
        info = f"Pi1 | Hailo-8 | {LOCATION} | {len(detections)} obj"
        cv2.putText(frame, info, (8, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
        ts = datetime.now().strftime("%H:%M:%S")
        cv2.putText(frame, ts, (frame.shape[1] - 75, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)

        return frame

    def publish_detections(self, detections):
        """Publish detections via MQTT with throttling."""
        now = time.time()
        current_classes = {d["class"] for d in detections}

        # Publish if classes changed or throttle interval elapsed
        if current_classes != self.last_classes or (now - self.last_publish_time) >= MQTT_THROTTLE_S:
            payload = {
                "camera_id": CAMERA_ID,
                "location": LOCATION,
                "timestamp": datetime.now().isoformat(),
                "objects": detections,
                "frame_count": self.frame_count,
            }
            topic = f"sentient/sensor/vision/{CAMERA_ID}/detection"
            self.mqtt_client.publish(topic, json.dumps(payload), qos=0)
            self.last_publish_time = now
            self.last_classes = current_classes

    def run(self):
        """Main detection loop."""
        if not HAS_HAILO:
            logger.error("Hailo/Picamera2 not available - run on Pi5 hardware")
            return

        logger.info("Starting vision detector...")
        self.connect_mqtt()
        self.start_mjpeg()

        # Initialize camera
        picam2 = Picamera2()
        config = picam2.create_still_configuration(
            main={"size": CAMERA_SIZE, "format": "RGB888"}
        )
        picam2.configure(config)
        picam2.start()
        logger.info(f"Camera started: {CAMERA_SIZE}")
        time.sleep(1)  # Camera warm-up

        # Load Hailo model
        hef = HEF(MODEL_PATH)
        devices = VDevice()
        configure_params = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
        network_group = devices.configure(hef, configure_params)[0]

        input_vstream_params = InputVStreamParams.make(network_group)
        output_vstream_params = OutputVStreamParams.make(network_group)

        input_vstreams_info = hef.get_input_vstream_infos()
        input_shape = input_vstreams_info[0].shape  # (H, W, C)
        model_h, model_w = input_shape[0], input_shape[1]

        logger.info(f"Hailo model loaded: {MODEL_PATH} ({model_w}x{model_h})")
        logger.info(f"MJPEG streaming on port {MJPEG_PORT}")

        fps_time = time.time()
        fps_frames = 0

        with InferVStreams(network_group, input_vstream_params, output_vstream_params) as pipeline:
            while self.running:
                try:
                    # Capture frame
                    frame = picam2.capture_array()
                    self.frame_count += 1
                    fps_frames += 1

                    # Prepare input (resize if needed)
                    if frame.shape[:2] != (model_h, model_w):
                        input_frame = cv2.resize(frame, (model_w, model_h))
                    else:
                        input_frame = frame

                    # Run inference
                    input_data = {input_vstreams_info[0].name: np.expand_dims(input_frame, axis=0)}
                    raw_output = pipeline.infer(input_data)

                    # Parse detections from NMS output
                    output_key = list(raw_output.keys())[0]
                    raw_dets = raw_output[output_key][0]  # First batch
                    detections = self.parse_hailo_detections(raw_dets, CAMERA_SIZE[0], CAMERA_SIZE[1])

                    # Draw overlays on frame for MJPEG
                    display_frame = self.draw_detections(frame.copy(), detections)

                    # Encode and serve via MJPEG
                    _, jpeg = cv2.imencode('.jpg', display_frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    self.mjpeg_server.update_frame(jpeg.tobytes())

                    # Publish to MQTT
                    self.publish_detections(detections)

                    # Log FPS every 10 seconds
                    elapsed = time.time() - fps_time
                    if elapsed >= 10:
                        fps = fps_frames / elapsed
                        logger.info(f"FPS: {fps:.1f} | Detections: {len(detections)} | Frames: {self.frame_count}")
                        fps_time = time.time()
                        fps_frames = 0

                except Exception as e:
                    logger.error(f"Detection loop error: {e}", exc_info=True)
                    time.sleep(0.1)

        # Cleanup
        logger.info("Shutting down...")
        picam2.stop()
        if self.mjpeg_server:
            self.mjpeg_server.stop()
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        logger.info("Vision detector stopped")


if __name__ == "__main__":
    detector = VisionDetector()
    detector.run()
