import os
import json
import asyncio
from datetime import datetime
from typing import Optional, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from exporter import trigger_full_export

app = FastAPI(title="PwC Hybrid Export Service")


class TriggerRequest(BaseModel):
    session_id: str
    storage_state: Optional[Dict] = None
    api_map: Optional[Dict] = None

    @field_validator('storage_state', mode='before')
    @classmethod
    def parse_storage_state(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                raise ValueError("storage_state is not valid JSON")
        if isinstance(v, dict):
            return v
        raise ValueError("storage_state must be dict or JSON string")

    @field_validator('api_map', mode='before')
    @classmethod
    def parse_api_map(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                raise ValueError("api_map is not valid JSON")
        if isinstance(v, dict):
            return v
        raise ValueError("api_map must be dict or JSON string")


export_lock = asyncio.Lock()
export_in_progress = False


@app.get("/health")
async def health():
    return {"ok": True, "timestamp": datetime.utcnow().isoformat()}


@app.post("/trigger-fetch")
async def trigger_fetch(req: TriggerRequest):
    global export_in_progress
    if export_in_progress:
        raise HTTPException(status_code=429, detail="Export already in progress")

    async with export_lock:
        if export_in_progress:
            raise HTTPException(status_code=429, detail="Export already in progress")
        export_in_progress = True
        try:
            result = await trigger_full_export(
                session_id=req.session_id,
                storage_state=req.storage_state,
                api_map=req.api_map,
                spreadsheet_id=os.getenv("GOOGLE_SHEET_ID"),
                drive_folder_id=os.getenv("GOOGLE_DRIVE_FOLDER_ID")
            )
            return JSONResponse(content=result)
        finally:
            export_in_progress = False


@app.post("/upload-to-sheets")
async def upload_to_sheets():
    from exporter import upload_existing_to_sheets
    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not spreadsheet_id:
        raise HTTPException(status_code=400, detail="GOOGLE_SHEET_ID env not set")
    result = await upload_existing_to_sheets(spreadsheet_id)
    return JSONResponse(content=result)


