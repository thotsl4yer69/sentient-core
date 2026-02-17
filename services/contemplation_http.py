#!/usr/bin/env python3
"""
Contemplation HTTP API - FastAPI Wrapper for ContemplationEngine

Exposes the multi-voice contemplative reasoning engine via HTTP endpoints.
Provides RESTful access to Cortana's internal dialogue system.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from contemplation import (
    ContemplationEngine,
    ContemplationConfig,
    ContemplationResult,
    InputType,
    EmotionState,
    ExpressionHints
)

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("contemplation_http")


# Request/Response Models
class GenerateRequest(BaseModel):
    """Request model for /generate endpoint"""
    input: str = Field(..., description="Text input to contemplate")
    user_id: Optional[str] = Field(None, description="User identifier for context")
    memories: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Recent conversation context")
    world_state: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Current world state")
    conversation_context: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Conversation metadata")


class VoiceResponse(BaseModel):
    """Individual voice perspective in response"""
    content: str
    generation_time_ms: float


class EmotionResponse(BaseModel):
    """Emotion state in response"""
    primary: str
    valence: float
    arousal: float
    intensity: float
    confidence: float


class ExpressionResponse(BaseModel):
    """Expression hints in response"""
    gestures: List[str]
    tone: str
    pacing: str


class ThinkingProcessResponse(BaseModel):
    """Complete thinking process breakdown"""
    voices: Dict[str, VoiceResponse]
    total_time_ms: float


class GenerateResponse(BaseModel):
    """Response model for /generate endpoint"""
    response: str = Field(..., description="Synthesized response text")
    emotion: EmotionResponse = Field(..., description="Detected emotional state")
    expression: ExpressionResponse = Field(..., description="Physical expression cues")
    thinking_process: ThinkingProcessResponse = Field(..., description="Internal voice perspectives")


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    timestamp: str
    ollama_host: str
    contemplation_count: int


# Global engine instance
engine: Optional[ContemplationEngine] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - startup and shutdown"""
    global engine

    # Startup: Initialize ContemplationEngine
    logger.info("Starting Contemplation HTTP API...")

    config = ContemplationConfig(
        ollama_host="http://localhost:11434",
        ollama_model="llama3.2:1b",
        synthesis_model="llama3.2:1b",
        memory_context_file=Path("/home/cortana/sentient-core/data/memory/contemplation_memory.json"),
        personality_prompt_file=Path("/opt/sentient-core/personality/cortana_core.txt")
    )

    engine = ContemplationEngine(config)
    await engine.start()

    logger.info("Contemplation HTTP API ready on port 8002")

    yield

    # Shutdown: Clean up engine
    logger.info("Stopping Contemplation HTTP API...")
    if engine:
        await engine.stop()
    logger.info("Contemplation HTTP API stopped")


# FastAPI application
app = FastAPI(
    title="Contemplation HTTP API",
    description="HTTP wrapper for Cortana's multi-voice contemplative reasoning engine",
    version="1.0.0",
    lifespan=lifespan
)


# CORS middleware - allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint

    Returns service status and basic metrics.
    """
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")

    return HealthResponse(
        status="healthy",
        timestamp=datetime.now().isoformat(),
        ollama_host=engine.config.ollama_host,
        contemplation_count=engine._contemplation_count
    )


@app.post("/generate", response_model=GenerateResponse)
async def generate_response(request: GenerateRequest):
    """
    Generate contemplative response

    Processes input through the multi-voice contemplation engine and returns:
    - Synthesized natural language response
    - Detected emotional state
    - Physical expression hints
    - Complete thinking process (all voice perspectives)

    The engine uses five internal voices:
    - OBSERVER: Raw factual observations
    - ANALYST: Logical implications
    - EMPATH: Emotional resonance
    - SKEPTIC: Alternative interpretations
    - MEMORY: Past context connections
    """
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")

    try:
        # Add conversation context to memory if provided
        if request.memories:
            for memory in request.memories:
                # Extract text content from memory dict
                memory_text = memory.get('content', '') or memory.get('text', '') or str(memory)
                if memory_text:
                    engine.memory.add_memory(memory_text, category="conversation")

        # Perform contemplation
        result: ContemplationResult = await engine.contemplate(
            input_text=request.input,
            input_type=InputType.TEXT
        )

        # Convert voices to response format
        voices_dict = {
            voice_name: VoiceResponse(
                content=perspective.content,
                generation_time_ms=perspective.generation_time_ms
            )
            for voice_name, perspective in result.perspectives.items()
        }

        # Build response
        response = GenerateResponse(
            response=result.synthesized_response,
            emotion=EmotionResponse(
                primary=result.emotion.primary.value,
                valence=result.emotion.valence,
                arousal=result.emotion.arousal,
                intensity=result.emotion.intensity,
                confidence=result.emotion.confidence
            ),
            expression=ExpressionResponse(
                gestures=result.expression.gestures,
                tone=result.expression.tone,
                pacing=result.expression.pacing
            ),
            thinking_process=ThinkingProcessResponse(
                voices=voices_dict,
                total_time_ms=result.total_time_ms
            )
        )

        logger.info(
            f"Generated response for '{request.input[:30]}...' in {result.total_time_ms:.0f}ms "
            f"(emotion: {result.emotion.primary.value})"
        )

        return response

    except Exception as e:
        logger.error(f"Error generating response: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate response: {str(e)}")


@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "service": "Contemplation HTTP API",
        "version": "1.0.0",
        "description": "HTTP wrapper for Cortana's multi-voice contemplative reasoning engine",
        "endpoints": {
            "/health": "GET - Health check",
            "/generate": "POST - Generate contemplative response",
            "/docs": "GET - Interactive API documentation"
        }
    }


def main():
    """Entry point for running the server"""
    uvicorn.run(
        "contemplation_http:app",
        host="0.0.0.0",
        port=8002,
        reload=False,
        log_level="info"
    )


if __name__ == "__main__":
    main()
