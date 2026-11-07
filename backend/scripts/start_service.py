"""Production entry point: explicit assets, no downloads, seeding or migrations."""
import argparse
import importlib.util
import os
from pathlib import Path
import sys
from backend.app.config import settings


def validate_assets(config):
    if config.embedding_provider != "lexical":
        root = Path(config.embedding_model_path)
        if not importlib.util.find_spec("onnxruntime") or not all((root/f).is_file()
                for f in ("manifest.json","model_quantized.onnx","tokenizer.json")):
            raise ValueError("Provision the approved E5 ONNX runtime/assets before starting this profile.")
    if config.speech_enabled:
        root = Path(config.speech_model_path)
        if not importlib.util.find_spec("faster_whisper") or not all((root/f).is_file()
                for f in ("model.bin","config.json","tokenizer.json","vocabulary.txt")):
            raise ValueError("Provision the approved local Whisper runtime/assets before enabling speech.")
    if config.ocr_enabled:
        from backend.app.ocr import readiness
        if not readiness(config.ocr_tessdata_path,config.ocr_languages)["ready"]:
            raise ValueError("Provision the selected approved OCR language packs before enabling OCR.")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service",choices=["api","worker"])
    args=parser.parse_args()
    config=settings()
    if config.app_env != "production":
        raise ValueError("This deployment entry point requires explicit production configuration.")
    config.validate_production()
    validate_assets(config)
    if args.service == "api":
        import uvicorn
        uvicorn.run("backend.app.main:app",host="0.0.0.0",port=int(os.environ.get("PORT","8000")),access_log=False)
    else:
        from backend.app.worker import main as worker
        sys.argv=[sys.argv[0]]
        worker()


if __name__ == "__main__":
    main()
