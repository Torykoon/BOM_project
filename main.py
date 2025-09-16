#!/usr/bin/env python3
"""
식품안전법령 검증 시스템 v2.0 - 디버깅 강화 버전
MongoDB + WebSocket + AutoGen 0.4 + MarkItDown + MessageFilter + Comprehensive Debugging

핵심 개선사항:
- MessageFilterAgent를 통한 에이전트 환각 방지
- TaskDistributor가 Markdown 전체를 분석하여 원료명 추출
- MongoDB 검색 결과를 raw JSON으로 전달
- 명확한 에이전트 간 메시지 라우팅
- 종합 디버깅 시스템 (로깅, 모니터링, 추적)

디버깅 기능:
- 에이전트별 모든 메시지 완전 기록
- MongoDB 쿼리 및 결과 상세 로깅
- 실시간 워크플로우 추적
- 세션별 JSON 로그 파일 생성
- 웹 인터페이스 디버깅 패널
- 콘솔 상세 로그 출력

필요한 패키지:
pip install "autogen-agentchat>=0.4.5" "autogen-ext[openai]>=0.4.5" 
pip install fastapi uvicorn websockets motor markitdown
pip install python-multipart

실행:
python food_safety_system_v2_debug.py
"""

import asyncio
import json
import tempfile
import os
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
import logging
from dotenv import load_dotenv

# FastAPI 관련
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse

# MongoDB 관련
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

# MarkItDown 관련
from markitdown import MarkItDown

# AutoGen 관련
from autogen_agentchat.agents import AssistantAgent, MessageFilterAgent, MessageFilterConfig, PerSourceFilter
from autogen_agentchat.teams import DiGraphBuilder, GraphFlow
from autogen_agentchat.conditions import MaxMessageTermination
from autogen_agentchat.messages import TextMessage
from autogen_ext.models.openai import OpenAIChatCompletionClient

# ===============================
# 설정
# ===============================
load_dotenv()

# MongoDB 설정
MONGO_CONFIG = {
    "url": os.getenv("MONGO_URL"),
    "database": os.getenv("MONGO_DATABASE"),
    "collection": os.getenv("MONGO_COLLECTION")
}

# OpenAI API Key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 전역 디버거 저장소
_current_debuggers = {}

def set_current_debugger(session_id: str, debugger):
    """현재 세션의 디버거 설정"""
    _current_debuggers[session_id] = debugger

def get_current_debugger(session_id: str = None):
    """현재 세션의 디버거 가져오기"""
    if session_id and session_id in _current_debuggers:
        return _current_debuggers[session_id]
    # 세션 ID가 없거나 찾을 수 없으면 가장 최근 디버거 반환
    if _current_debuggers:
        return list(_current_debuggers.values())[-1]
    return None

# ===============================
# 디버깅 시스템
# ===============================

class DebugLogger:
    """종합 디버깅 로거"""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.log_dir = Path("debug_logs")
        self.log_dir.mkdir(exist_ok=True)
        
        # 세션별 로그 파일
        self.log_file = self.log_dir / f"session_{session_id}.json"
        self.debug_data = {
            "session_id": session_id,
            "start_time": datetime.now().isoformat(),
            "agents": {},
            "workflow_steps": [],
            "mongodb_queries": [],
            "message_flow": [],
            "errors": [],
            "performance": {}
        }
        print(f"🔍 디버깅 로거 초기화: {self.log_file}")
    
    def log_agent_message(self, agent_name: str, message_type: str, content: str, metadata: dict = None):
        """에이전트 메시지 로깅"""
        if agent_name not in self.debug_data["agents"]:
            self.debug_data["agents"][agent_name] = {"messages": [], "total_messages": 0}
        
        message_data = {
            "timestamp": datetime.now().isoformat(),
            "type": message_type,
            "content": content,
            "content_length": len(content),
            "metadata": metadata or {}
        }
        
        self.debug_data["agents"][agent_name]["messages"].append(message_data)
        self.debug_data["agents"][agent_name]["total_messages"] += 1
        
        # 메시지 플로우에도 추가
        self.debug_data["message_flow"].append({
            "timestamp": datetime.now().isoformat(),
            "agent": agent_name,
            "type": message_type,
            "content_preview": content[:100] + "..." if len(content) > 100 else content
        })
        
        self._save_log()
        print(f"[DEBUG-LOG] {agent_name} ({message_type}): {len(content)} chars")
    
    def log_mongodb_query(self, ingredients: list, results: list, execution_time: float):
        """MongoDB 쿼리 로깅"""
        query_data = {
            "timestamp": datetime.now().isoformat(),
            "ingredients": ingredients,
            "results_count": len(results),
            "execution_time_ms": round(execution_time * 1000, 2),
            "results": results,
            "found_ingredients": [r.get('품목명', 'Unknown') for r in results]
        }
        
        self.debug_data["mongodb_queries"].append(query_data)
        self._save_log()
        
        print(f"[MONGO-LOG] 검색: {ingredients} -> {len(results)}개 결과 ({execution_time:.3f}초)")
        for result in results:
            print(f"  ✓ 발견: {result.get('품목명', 'Unknown')}")
    
    def log_workflow_step(self, step_name: str, details: dict):
        """워크플로우 단계 로깅"""
        step_data = {
            "timestamp": datetime.now().isoformat(),
            "step": step_name,
            "details": details
        }
        
        self.debug_data["workflow_steps"].append(step_data)
        self._save_log()
        
        print(f"[WORKFLOW-LOG] {step_name}: {details}")
    
    def log_error(self, function_name: str, error: str, context: dict = None):
        """에러 로깅"""
        error_data = {
            "timestamp": datetime.now().isoformat(),
            "function": function_name,
            "error": str(error),
            "context": context or {}
        }
        
        self.debug_data["errors"].append(error_data)
        self._save_log()
        
        print(f"[ERROR-LOG] {function_name}: {error}")
    
    def log_performance(self, metric_name: str, value: float, unit: str = "ms"):
        """성능 메트릭 로깅"""
        if "metrics" not in self.debug_data["performance"]:
            self.debug_data["performance"]["metrics"] = {}
        
        if metric_name not in self.debug_data["performance"]["metrics"]:
            self.debug_data["performance"]["metrics"][metric_name] = []
        
        self.debug_data["performance"]["metrics"][metric_name].append({
            "timestamp": datetime.now().isoformat(),
            "value": value,
            "unit": unit
        })
        
        self._save_log()
        print(f"[PERF-LOG] {metric_name}: {value}{unit}")
    
    def get_summary(self):
        """디버깅 요약 정보"""
        total_agents = len(self.debug_data["agents"])
        total_messages = sum(agent["total_messages"] for agent in self.debug_data["agents"].values())
        total_queries = len(self.debug_data["mongodb_queries"])
        total_errors = len(self.debug_data["errors"])
        
        return {
            "session_id": self.session_id,
            "duration": (datetime.now() - datetime.fromisoformat(self.debug_data["start_time"])).total_seconds(),
            "total_agents": total_agents,
            "total_messages": total_messages,
            "total_mongodb_queries": total_queries,
            "total_errors": total_errors,
            "log_file": str(self.log_file)
        }
    
    def _save_log(self):
        """로그 파일 저장"""
        try:
            with open(self.log_file, 'w', encoding='utf-8') as f:
                json.dump(self.debug_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"로그 파일 저장 실패: {e}")

# ===============================
# FastAPI 앱 생성
# ===============================

app = FastAPI(title="식품안전법령 검증 시스템 v2.0 (디버깅 강화)", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================
# MongoDB 연결 관리
# ===============================

class MongoDBManager:
    """MongoDB 연결 및 쿼리 관리"""
    
    def __init__(self):
        self.client = None
        self.db = None
        self.collection = None
    
    async def connect(self):
        """MongoDB 연결"""
        self.client = AsyncIOMotorClient(MONGO_CONFIG["url"])
        self.db = self.client[MONGO_CONFIG["database"]]
        self.collection = self.db[MONGO_CONFIG["collection"]]
        print("✅ MongoDB 연결 성공")
    
    async def disconnect(self):
        """MongoDB 연결 해제"""
        if self.client:
            self.client.close()
            print("MongoDB 연결 해제")
    
    async def search_ingredients(self, ingredient_names: List[str]) -> List[Dict]:
        """원료명으로 식품안전법령 정보 검색 (디버깅 강화)"""
        try:
            start_time = time.time()
            
            # 여러 원료명을 한번에 검색
            query = {"품목명": {"$in": ingredient_names}}
            cursor = self.collection.find(query)
            results = await cursor.to_list(length=None)
            
            # ObjectId를 문자열로 변환 (JSON 직렬화를 위해)
            for result in results:
                if "_id" in result:
                    result["_id"] = str(result["_id"])
            
            execution_time = time.time() - start_time
            
            # 디버깅 로그 (현재 세션의 디버거가 있다면)
            debugger = get_current_debugger()
            if debugger:
                debugger.log_mongodb_query(ingredient_names, results, execution_time)
            
            return results
            
        except Exception as e:
            print(f"MongoDB 검색 오류: {e}")
            debugger = get_current_debugger()
            if debugger:
                debugger.log_error("mongodb_search", str(e), {"ingredients": ingredient_names})
            return []
    
    async def insert_sample_data(self):
        """샘플 데이터 삽입 (테스트용)"""
        sample_data = [
            {
                "품목명": "가교카복시메틸셀룰로스나트륨",
                "사용기준": "가교카복시메틸셀룰로스나트륨은 건강기능식품(정제 또는 이의 제피, 캡슐에 한함) 및 캡슐류의 피막제 목적에 한하여 사용하여야 한다.",
                "주용도": ["피막제"],
                "파일명": "1.pdf"
            },
            {
                "품목명": "감색소",
                "사용기준": "감색소는 아래의 식품에 사용하여서는 아니 된다.\n1. 천연식품\n2. 다류\n3. 커피\n4. 고춧가루, 실고추\n5. 김치류\n6. 고추장, 조미고추장\n7. 식초",
                "주용도": ["착색료"],
                "파일명": "1.pdf"
            },
            {
                "품목명": "구연산",
                "사용기준": "구연산은 식품에 필요에 따라 적당량을 사용할 수 있다.",
                "주용도": ["산도조절제", "산미료"],
                "파일명": "2.pdf"
            },
            {
                "품목명": "산화마그네슘",
                "사용기준": "산화마그네슘은 식품제조 또는 가공상 필요시 적당량 사용할 수 있다.",
                "주용도": ["영양강화제", "pH조정제"],
                "파일명": "2.pdf"
            },
            {
                "품목명": "리포좀비타민C",
                "사용기준": "건강기능식품 원료로 사용 가능. 1일 섭취량 1000mg 이하.",
                "주용도": ["영양강화제"],
                "파일명": "3.pdf"
            }
        ]
        
        # 기존 데이터 확인 후 없으면 삽입
        for data in sample_data:
            existing = await self.collection.find_one({"품목명": data["품목명"]})
            if not existing:
                await self.collection.insert_one(data)
        print("✅ 샘플 데이터 준비 완료")

mongo_manager = MongoDBManager()

# ===============================
# WebSocket 연결 관리
# ===============================

class ConnectionManager:
    """WebSocket 연결 관리 (디버깅 강화)"""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.active_connections[session_id] = websocket
        print(f"✅ WebSocket 연결: {session_id}")
    
    def disconnect(self, session_id: str):
        if session_id in self.active_connections:
            del self.active_connections[session_id]
            # 디버거도 정리
            if session_id in _current_debuggers:
                debugger = _current_debuggers[session_id]
                print(f"🔍 디버깅 세션 종료: {debugger.get_summary()}")
                del _current_debuggers[session_id]
            print(f"WebSocket 연결 해제: {session_id}")
    
    async def send_message(self, session_id: str, message: dict):
        """특정 세션에 메시지 전송 (디버깅 로그 포함)"""
        if session_id in self.active_connections:
            try:
                await self.active_connections[session_id].send_text(json.dumps(message, ensure_ascii=False))
                
                # 디버깅 로그
                debugger = get_current_debugger(session_id)
                if debugger and message.get("type") != "debug_info":  # 디버그 메시지는 로그하지 않음 (무한루프 방지)
                    debugger.log_agent_message("WebSocket", "send", f"Type: {message.get('type')}", message)
                
            except Exception as e:
                print(f"메시지 전송 오류: {e}")
                debugger = get_current_debugger(session_id)
                if debugger:
                    debugger.log_error("websocket_send", str(e), {"message_type": message.get("type")})

manager = ConnectionManager()

# ===============================
# MongoDB 도구 함수 (AutoGen 에이전트용) - 디버깅 강화
# ===============================

async def mongodb_search_ingredients(ingredient_names: List[str]) -> str:
    """
    MongoDB에서 원료명으로 식품안전법령 정보 검색 (디버깅 강화)
    
    Args:
        ingredient_names: 검색할 원료명 리스트
    Returns:
        JSON 형태의 검색 결과 문자열
    """
    start_time = time.time()
    
    try:
        # 디버거 가져오기
        debugger = get_current_debugger()
        
        print(f"\n{'🔍 MongoDB 검색 시작'}")
        print(f"검색 원료: {ingredient_names}")
        
        results = await mongo_manager.search_ingredients(ingredient_names)
        execution_time = time.time() - start_time
        
        print(f"검색 완료: {len(results)}개 결과, {execution_time:.3f}초")
        
        # 상세 결과 출력
        if results:
            print("발견된 원료:")
            for i, result in enumerate(results, 1):
                ingredient_name = result.get('품목명', 'Unknown')
                usage_standard = result.get('사용기준', 'No standard')[:100]
                print(f"  {i}. {ingredient_name}")
                print(f"     사용기준: {usage_standard}...")
        else:
            print(f"⚠️ 검색 결과 없음: {ingredient_names}")
        
        # 성능 메트릭 로그
        if debugger:
            debugger.log_performance("mongodb_search_time", execution_time * 1000, "ms")
        
        if not results:
            response_data = {
                "status": "no_results",
                "searched_ingredients": ingredient_names,
                "execution_time_ms": round(execution_time * 1000, 2),
                "results": []
            }
        else:
            response_data = {
                "status": "success",
                "searched_ingredients": ingredient_names,
                "count": len(results),
                "execution_time_ms": round(execution_time * 1000, 2),
                "results": results
            }
        
        response_json = json.dumps(response_data, ensure_ascii=False)
        print(f"응답 크기: {len(response_json)} bytes\n")
        
        return response_json
        
    except Exception as e:
        execution_time = time.time() - start_time
        error_msg = f"MongoDB 검색 실패: {str(e)}"
        print(f"❌ {error_msg}")
        
        debugger = get_current_debugger()
        if debugger:
            debugger.log_error("mongodb_search_ingredients", str(e), {
                "ingredients": ingredient_names,
                "execution_time": execution_time
            })
        
        return json.dumps({
            "status": "error",
            "message": str(e),
            "searched_ingredients": ingredient_names,
            "execution_time_ms": round(execution_time * 1000, 2)
        }, ensure_ascii=False)

# ===============================
# AutoGen 워크플로우 (디버깅 강화)
# ===============================

class FoodSafetyWorkflow:
    """식품안전법령 검증 워크플로우 (디버깅 강화)"""
    
    def __init__(self, session_id: str, markdown_content: str):
        self.session_id = session_id
        self.markdown_content = markdown_content
        self.debugger = DebugLogger(session_id)
        self.client = OpenAIChatCompletionClient(
            model="gpt-5-nano",
            api_key=OPENAI_API_KEY
        )
        
        # 전역 디버거 설정
        set_current_debugger(session_id, self.debugger)
        
        print(f"🚀 워크플로우 초기화 완료: {session_id}")
        self.debugger.log_workflow_step("workflow_init", {
            "session_id": session_id,
            "markdown_length": len(markdown_content)
        })
    
    async def send_status(self, agent_name: str, message: str, status: str = "processing", debug_data: dict = None):
        """상태 전송 + 디버깅 로그"""
        # WebSocket 전송
        await manager.send_message(self.session_id, {
            "type": "agent_status",
            "agent": agent_name,
            "message": message,
            "status": status,
            "timestamp": datetime.now().isoformat()
        })
        
        # 디버깅 로그 추가
        self.debugger.log_agent_message(agent_name, "status", message, {
            "status": status,
            "debug_data": debug_data or {}
        })
        
        # 콘솔 출력 강화
        status_emoji = {"started": "🚀", "processing": "⚙️", "completed": "✅", "error": "❌"}
        emoji = status_emoji.get(status, "📝")
        print(f"{emoji} [{agent_name}] {message}")
        
        if debug_data:
            print(f"   DEBUG: {json.dumps(debug_data, ensure_ascii=False, indent=2)}")
    
    async def send_debug_info(self, debug_type: str, data: dict):
        """디버깅 정보 전용 WebSocket 전송"""
        await manager.send_message(self.session_id, {
            "type": "debug_info",
            "debug_type": debug_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        })
    
    async def create_agents(self):
        """AutoGen 에이전트 생성 (MessageFilter 적용)"""
        
        print("\n🤖 에이전트 생성 시작...")
        self.debugger.log_workflow_step("agents_creation_start", {})
        
        # TaskDistributor: Markdown 분석 및 원료명 추출/분배
        task_distributor_core = AssistantAgent(
            name="TaskDistributor",
            model_client=self.client,
            system_message=f"""당신은 원료명 추출 및 작업 분배 관리자입니다.
            
            주어진 Markdown 데이터를 분석하여 원료명을 추출하고 3개의 ProcessorB 에이전트에게 균등하게 분배합니다.
            
            === Markdown 데이터 ===
            {self.markdown_content}
            === 데이터 끝 ===
            
            작업 순서:
            1. 위 Markdown 데이터에서 모든 원료명(원재료명, 성분명)을 추출하세요.
            2. 추출한 원료명을 3개 그룹으로 균등하게 나누세요.
            3. 각 ProcessorB에게 작업을 할당하세요.
            
            다음 정확한 형식으로 메시지를 보내세요:
            "@ProcessorB1: ['원료명1', '원료명2', ...]를 검색하세요."
            "@ProcessorB2: ['원료명3', '원료명4', ...]를 검색하세요."
            "@ProcessorB3: ['원료명5', '원료명6', ...]를 검색하세요."
            
            주의사항:
            - 반드시 각 에이전트명 앞에 @를 붙이세요.
            - 원료명은 리스트 형태로 전달하세요.
            - 모든 지시사항을 하나의 메시지에 포함시키세요."""
        )
        
        task_distributor = task_distributor_core
        
        # ProcessorB1: MongoDB 검색 (MessageFilter 적용)
        processor_b1_core = AssistantAgent(
            name="ProcessorB1",
            model_client=self.client,
            system_message="""당신은 ProcessorB1 MongoDB 검색 전문가입니다.
            
            ⚠️ 중요한 규칙:
            1. "@ProcessorB1:"이 포함된 메시지의 작업만 수행하세요.
            2. 다른 에이전트(@ProcessorB2, @ProcessorB3)에게 할당된 작업은 절대 수행하지 마세요.
            3. 메시지에서 "@ProcessorB1:" 다음에 오는 원료명 리스트만 추출하여 검색하세요.
            
            작업 수행 방법:
            - "@ProcessorB1:" 뒤에 명시된 원료명 리스트를 찾으세요.
            - 해당 원료명들을 mongodb_search_ingredients 도구로 검색하세요.
            - 검색 결과를 그대로 보고하세요 (가공하지 마세요).
            - 마지막에 "ProcessorB1_검색완료"를 포함하여 완료를 알리세요.""",
            tools=[mongodb_search_ingredients]
        )
        
        processor_b1 = MessageFilterAgent(
            name="ProcessorB1",
            wrapped_agent=processor_b1_core,
            filter=MessageFilterConfig(
                per_source=[
                    PerSourceFilter(source="TaskDistributor", position="last", count=1),
                ]
            )
        )
        
        # ProcessorB2: MongoDB 검색 (MessageFilter 적용)
        processor_b2_core = AssistantAgent(
            name="ProcessorB2",
            model_client=self.client,
            system_message="""당신은 ProcessorB2 MongoDB 검색 전문가입니다.
            
            ⚠️ 중요한 규칙:
            1. "@ProcessorB2:"가 포함된 메시지의 작업만 수행하세요.
            2. 다른 에이전트(@ProcessorB1, @ProcessorB3)에게 할당된 작업은 절대 수행하지 마세요.
            3. 메시지에서 "@ProcessorB2:" 다음에 오는 원료명 리스트만 추출하여 검색하세요.
            
            작업 수행 방법:
            - "@ProcessorB2:" 뒤에 명시된 원료명 리스트를 찾으세요.
            - 해당 원료명들을 mongodb_search_ingredients 도구로 검색하세요.
            - 검색 결과를 그대로 보고하세요 (가공하지 마세요).
            - 마지막에 "ProcessorB2_검색완료"를 포함하여 완료를 알리세요.""",
            tools=[mongodb_search_ingredients]
        )
        
        processor_b2 = MessageFilterAgent(
            name="ProcessorB2",
            wrapped_agent=processor_b2_core,
            filter=MessageFilterConfig(
                per_source=[
                    PerSourceFilter(source="TaskDistributor", position="last", count=1),
                ]
            )
        )
        
        # ProcessorB3: MongoDB 검색 (MessageFilter 적용)
        processor_b3_core = AssistantAgent(
            name="ProcessorB3",
            model_client=self.client,
            system_message="""당신은 ProcessorB3 MongoDB 검색 전문가입니다.
            
            ⚠️ 중요한 규칙:
            1. "@ProcessorB3:"이 포함된 메시지의 작업만 수행하세요.
            2. 다른 에이전트(@ProcessorB1, @ProcessorB2)에게 할당된 작업은 절대 수행하지 마세요.
            3. 메시지에서 "@ProcessorB3:" 다음에 오는 원료명 리스트만 추출하여 검색하세요.
            
            작업 수행 방법:
            - "@ProcessorB3:" 뒤에 명시된 원료명 리스트를 찾으세요.
            - 해당 원료명들을 mongodb_search_ingredients 도구로 검색하세요.
            - 검색 결과를 그대로 보고하세요 (가공하지 마세요).
            - 마지막에 "ProcessorB3_검색완료"를 포함하여 완료를 알리세요.""",
            tools=[mongodb_search_ingredients]
        )
        
        processor_b3 = MessageFilterAgent(
            name="ProcessorB3",
            wrapped_agent=processor_b3_core,
            filter=MessageFilterConfig(
                per_source=[
                    PerSourceFilter(source="TaskDistributor", position="last", count=1),
                ]
            )
        )
        
        # ComparatorC1: 법령 비교 분석 (MessageFilter 적용)
        comparator_c1_core = AssistantAgent(
            name="ComparatorC1",
            model_client=self.client,
            system_message=f"""당신은 ComparatorC1 식품안전법령 비교 전문가입니다.
            
            ProcessorB1의 검색 결과를 분석하여 법령 준수 여부를 판단합니다.
            
            === 원본 Markdown 데이터 (참고용) ===
            {self.markdown_content[:2000]}...
            === 데이터 끝 ===
            
            분석 방법:
            1. ProcessorB1이 제공한 MongoDB 검색 결과를 파싱하세요.
            2. 각 원료의 법령 정보를 분석하세요:
               - 품목명
               - 사용기준
               - 주용도
               - 제한사항이나 금지사항
            3. 원본 Markdown의 제품에서 해당 원료 사용이 적합한지 판단하세요.
            4. 각 원료별로 다음을 평가하세요:
               - ✅ 적합: 모든 법령 기준 충족
               - ⚠️ 조건부 적합: 특정 조건 하에 사용 가능
               - ❌ 부적합: 사용 금지 또는 기준 위반
            
            분석 완료 후 "ComparatorC1_분석완료"를 포함하세요."""
        )
        
        comparator_c1 = MessageFilterAgent(
            name="ComparatorC1",
            wrapped_agent=comparator_c1_core,
            filter=MessageFilterConfig(
                per_source=[
                    PerSourceFilter(source="ProcessorB1", position="last", count=1),
                ]
            )
        )
        
        # ComparatorC2: 법령 비교 분석 (MessageFilter 적용)
        comparator_c2_core = AssistantAgent(
            name="ComparatorC2",
            model_client=self.client,
            system_message=f"""당신은 ComparatorC2 식품안전법령 비교 전문가입니다.
            
            ProcessorB2의 검색 결과를 분석하여 법령 준수 여부를 판단합니다.
            
            === 원본 Markdown 데이터 (참고용) ===
            {self.markdown_content[:2000]}...
            === 데이터 끝 ===
            
            분석 방법:
            1. ProcessorB2가 제공한 MongoDB 검색 결과를 파싱하세요.
            2. 각 원료의 법령 정보를 분석하세요:
               - 품목명
               - 사용기준
               - 주용도
               - 제한사항이나 금지사항
            3. 원본 Markdown의 제품에서 해당 원료 사용이 적합한지 판단하세요.
            4. 각 원료별로 다음을 평가하세요:
               - ✅ 적합: 모든 법령 기준 충족
               - ⚠️ 조건부 적합: 특정 조건 하에 사용 가능
               - ❌ 부적합: 사용 금지 또는 기준 위반
            
            분석 완료 후 "ComparatorC2_분석완료"를 포함하세요."""
        )
        
        comparator_c2 = MessageFilterAgent(
            name="ComparatorC2",
            wrapped_agent=comparator_c2_core,
            filter=MessageFilterConfig(
                per_source=[
                    PerSourceFilter(source="ProcessorB2", position="last", count=1),
                ]
            )
        )
        
        # ComparatorC3: 법령 비교 분석 (MessageFilter 적용)
        comparator_c3_core = AssistantAgent(
            name="ComparatorC3",
            model_client=self.client,
            system_message=f"""당신은 ComparatorC3 식품안전법령 비교 전문가입니다.
            
            ProcessorB3의 검색 결과를 분석하여 법령 준수 여부를 판단합니다.
            
            === 원본 Markdown 데이터 (참고용) ===
            {self.markdown_content[:2000]}...
            === 데이터 끝 ===
            
            분석 방법:
            1. ProcessorB3이 제공한 MongoDB 검색 결과를 파싱하세요.
            2. 각 원료의 법령 정보를 분석하세요:
               - 품목명
               - 사용기준
               - 주용도
               - 제한사항이나 금지사항
            3. 원본 Markdown의 제품에서 해당 원료 사용이 적합한지 판단하세요.
            4. 각 원료별로 다음을 평가하세요:
               - ✅ 적합: 모든 법령 기준 충족
               - ⚠️ 조건부 적합: 특정 조건 하에 사용 가능
               - ❌ 부적합: 사용 금지 또는 기준 위반
            
            분석 완료 후 "ComparatorC3_분석완료"를 포함하세요."""
        )
        
        comparator_c3 = MessageFilterAgent(
            name="ComparatorC3",
            wrapped_agent=comparator_c3_core,
            filter=MessageFilterConfig(
                per_source=[
                    PerSourceFilter(source="ProcessorB3", position="last", count=1),
                ]
            )
        )
        
        # FinalCollector: 최종 판정 (MessageFilter 적용)
        final_collector_core = AssistantAgent(
            name="FinalCollector",
            model_client=self.client,
            system_message="""당신은 최종 식품안전 판정 전문가입니다.
            
            모든 Comparator(C1, C2, C3)의 분석 결과를 종합하여 최종 판정 보고서를 작성합니다.
            
            📋 **최종 판정 보고서 구성**
            
            1. ✅ **적합 원료 목록**
               - 모든 법령 기준을 충족하는 원료
               - 사용 제한이 없는 원료
               - 안전하게 사용 가능한 원료
            
            2. ⚠️ **조건부 적합 원료 목록**
               - 특정 조건 하에서만 사용 가능한 원료
               - 사용량 제한이 있는 원료
               - 추가 인증이 필요한 원료
               - 각 원료별 준수해야 할 조건 명시
            
            3. ❌ **부적합 원료 목록**
               - 법령 위반 원료
               - 사용 금지된 원료
               - 기준을 충족하지 못한 원료
               - **반드시 각 원료별 부적합 사유를 Comparator에이전트의 결과를 기반으로 설명**
            
            4. 📊 **종합 평가**
               - 전체 적합률 (적합 원료 수 / 전체 원료 수)
               - 주요 리스크 요인
               - 개선 권고사항
               - 추가 검토가 필요한 사항
            
            5. 💡 **권고사항**
               - 조건부 적합 원료의 사용 가이드라인
               - 부적합 원료의 대체 방안
               - 법령 준수를 위한 조치사항
            
            명확하고 실용적인 최종 보고서를 작성하세요.
            보고서 마지막에 "FinalCollector_최종판정완료"를 포함하세요."""
        )
        
        final_collector = MessageFilterAgent(
            name="FinalCollector",
            wrapped_agent=final_collector_core,
            filter=MessageFilterConfig(
                per_source=[
                    PerSourceFilter(source="ComparatorC1", position="last", count=1),
                    PerSourceFilter(source="ComparatorC2", position="last", count=1),
                    PerSourceFilter(source="ComparatorC3", position="last", count=1),
                ]
            )
        )
        
        agents = (task_distributor, processor_b1, processor_b2, processor_b3,
                 comparator_c1, comparator_c2, comparator_c3, final_collector)
        
        agent_names = [agent.name for agent in agents]
        print(f"✅ 에이전트 생성 완료: {agent_names}")
        
        self.debugger.log_workflow_step("agents_created", {
            "total_agents": len(agents),
            "agent_names": agent_names
        })
        
        return agents
    
    async def run_workflow(self):
        """워크플로우 실행 (디버깅 강화)"""
        try:
            workflow_start_time = time.time()
            
            await self.send_status("System", "디버깅 강화 워크플로우 시작", "started")
            self.debugger.log_workflow_step("workflow_start", {"markdown_length": len(self.markdown_content)})
            
            # 에이전트 생성
            agents = await self.create_agents()
            (task_distributor, processor_b1, processor_b2, processor_b3,
             comparator_c1, comparator_c2, comparator_c3, final_collector) = agents
            
            # GraphFlow 구성
            print("\n🔗 GraphFlow 구성 중...")
            builder = DiGraphBuilder()
            
            # 모든 노드 추가
            for agent in agents:
                builder.add_node(agent)
            
            # 엣지 연결
            # 1단계: TaskDistributor → ProcessorB1,B2,B3 (팬아웃)
            builder.add_edge(task_distributor, processor_b1)
            builder.add_edge(task_distributor, processor_b2)
            builder.add_edge(task_distributor, processor_b3)
            
            # 2단계: ProcessorB → ComparatorC (일대일 매핑)
            builder.add_edge(processor_b1, comparator_c1)
            builder.add_edge(processor_b2, comparator_c2)
            builder.add_edge(processor_b3, comparator_c3)
            
            # 3단계: ComparatorC → FinalCollector (팬인)
            builder.add_edge(comparator_c1, final_collector)
            builder.add_edge(comparator_c2, final_collector)
            builder.add_edge(comparator_c3, final_collector)
            
            # 진입점 설정
            builder.set_entry_point(task_distributor)
            
            # 그래프 빌드
            graph = builder.build()
            
            # GraphFlow 팀 생성
            flow = GraphFlow(
                participants=list(agents),
                graph=graph,
                termination_condition=MaxMessageTermination(30)
            )
            
            self.debugger.log_workflow_step("graph_built", {
                "node_count": len(agents),
                "edge_count": 7,  # 위에서 정의한 엣지 수
            })
            
            print("✅ GraphFlow 구성 완료")
            print("\n🚀 워크플로우 실행 시작...\n")
            
            # 워크플로우 실행 (스트리밍 + 상세 디버깅)
            message_count = 0
            async for message in flow.run_stream(
                task="""제공된 Markdown 데이터에서 원료명을 추출하고, 
                각 원료의 식품안전법령 적합성을 검증하세요.
                MongoDB에서 법령 정보를 검색하고, 
                최종적으로 적합/조건부적합/부적합 원료를 분류하여 보고하세요."""
            ):
                message_count += 1
                
                if isinstance(message, TextMessage):
                    # 상세 메시지 로깅
                    self.debugger.log_agent_message(
                        message.source, 
                        "workflow_message", 
                        message.content,
                        {
                            "message_id": message_count,
                            "content_length": len(message.content)
                        }
                    )
                    
                    # 콘솔에 상세 출력
                    print(f"\n{'='*80}")
                    print(f"📨 메시지 #{message_count} - {message.source}")
                    print(f"{'='*80}")
                    print(f"내용 길이: {len(message.content)} characters")
                    print(f"시간: {datetime.now().strftime('%H:%M:%S')}")
                    print(f"{'-'*80}")
                    
                    # 메시지 내용을 적절히 포맷팅하여 출력
                    if len(message.content) > 1000:
                        print(f"{message.content[:500]}")
                        print(f"\n... [중간 생략: {len(message.content) - 1000} characters] ...\n")
                        print(f"{message.content[-500:]}")
                    else:
                        print(message.content)
                    
                    print(f"{'='*80}\n")
                    
                    # 디버깅 정보 WebSocket 전송
                    await self.send_debug_info("agent_message", {
                        "message_id": message_count,
                        "source": message.source,
                        "content_length": len(message.content),
                        "timestamp": datetime.now().isoformat(),
                        "content_preview": message.content[:200] + "..." if len(message.content) > 200 else message.content
                    })
                    
                    # 기존 상태 전송 (축약된 버전)
                    content_preview = message.content[:300] if len(message.content) > 300 else message.content
                    await self.send_status(message.source, content_preview, "processing", {
                        "message_id": message_count,
                        "full_length": len(message.content)
                    })
                    
                    # 완료 키워드 체크
                    if "최종판정완료" in message.content:
                        print("\n🎉 최종 판정 완료! 결과를 전송합니다.\n")
                        
                        await manager.send_message(self.session_id, {
                            "type": "final_result",
                            "content": message.content,
                            "timestamp": datetime.now().isoformat()
                        })
                        
                        # 최종 성능 메트릭
                        workflow_total_time = time.time() - workflow_start_time
                        self.debugger.log_performance("total_workflow_time", workflow_total_time * 1000, "ms")
            
            # 워크플로우 완료
            workflow_total_time = time.time() - workflow_start_time
            self.debugger.log_workflow_step("workflow_completed", {
                "total_messages": message_count,
                "total_time_seconds": round(workflow_total_time, 2)
            })
            
            # 디버깅 요약 출력
            summary = self.debugger.get_summary()
            print(f"\n📊 워크플로우 완료 요약:")
            print(f"   총 소요시간: {summary['duration']:.2f}초")
            print(f"   총 에이전트: {summary['total_agents']}개")
            print(f"   총 메시지: {summary['total_messages']}개")
            print(f"   MongoDB 쿼리: {summary['total_mongodb_queries']}회")
            print(f"   에러 발생: {summary['total_errors']}회")
            print(f"   로그 파일: {summary['log_file']}")
            
            await self.send_status("System", f"워크플로우 완료 (총 {message_count}개 메시지, {workflow_total_time:.2f}초)", "completed", summary)
            
        except Exception as e:
            error_msg = f"워크플로우 오류: {str(e)}"
            self.debugger.log_error("run_workflow", str(e), {
                "session_id": self.session_id,
                "markdown_length": len(self.markdown_content)
            })
            await self.send_status("System", error_msg, "error")
            print(f"❌ {error_msg}")
            raise

# ===============================
# API 엔드포인트
# ===============================

@app.on_event("startup")
async def startup_event():
    """서버 시작 시 MongoDB 연결"""
    await mongo_manager.connect()
    await mongo_manager.insert_sample_data()

@app.on_event("shutdown")
async def shutdown_event():
    """서버 종료 시 MongoDB 연결 해제"""
    await mongo_manager.disconnect()

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """파일 업로드 및 Markdown 변환 (디버깅 강화)"""
    temp_file_path = None
    start_time = time.time()
    
    try:
        file_extension = os.path.splitext(file.filename)[1] if file.filename else ''
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        # MarkItDown으로 변환
        md = MarkItDown(enable_plugins=False)
        result = md.convert(temp_file_path)
        
        conversion_time = time.time() - start_time
        
        print(f"📄 파일 변환 완료:")
        print(f"   원본 파일: {file.filename} ({len(content)} bytes)")
        print(f"   변환 결과: {len(result.text_content)} characters")
        print(f"   변환 시간: {conversion_time:.3f}초")
        
        return {
            "filename": file.filename,
            "content": result.text_content,
            "status": "success",
            "metadata": {
                "original_size_bytes": len(content),
                "converted_size_chars": len(result.text_content),
                "conversion_time_ms": round(conversion_time * 1000, 2)
            }
        }
        
    except Exception as e:
        error_msg = f"파일 처리 실패: {str(e)}"
        print(f"❌ {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)
    
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket 엔드포인트 (디버깅 강화)"""
    await manager.connect(websocket, session_id)
    
    try:
        while True:
            # 클라이언트로부터 메시지 수신 
            data = await websocket.receive_text()
            message = json.loads(data)
            
            print(f"📨 WebSocket 메시지 수신: {message.get('type')} (세션: {session_id})")
            
            if message["type"] == "start_verification":
                # 검증 워크플로우 시작
                markdown_content = message["markdown_content"]
                workflow = FoodSafetyWorkflow(session_id, markdown_content)
                
                print(f"🚀 검증 워크플로우 시작: 세션 {session_id}")
                
                # 백그라운드에서 워크플로우 실행
                asyncio.create_task(workflow.run_workflow())
            
    except WebSocketDisconnect:
        manager.disconnect(session_id)
    except Exception as e:
        print(f"❌ WebSocket 오류: {e}")
        manager.disconnect(session_id)

@app.get("/download-log/{session_id}")
async def download_log(session_id: str):
    """세션별 디버깅 로그 다운로드"""
    log_file = Path(f"debug_logs/session_{session_id}.json")
    if log_file.exists():
        print(f"📥 로그 다운로드 요청: {log_file}")
        return FileResponse(
            log_file, 
            filename=f"debug_log_{session_id}.json",
            media_type="application/json"
        )
    else:
        raise HTTPException(status_code=404, detail="로그 파일을 찾을 수 없습니다")

@app.get("/debug-summary/{session_id}")
async def get_debug_summary(session_id: str):
    """세션별 디버깅 요약 정보"""
    if session_id in _current_debuggers:
        debugger = _current_debuggers[session_id]
        return debugger.get_summary()
    else:
        raise HTTPException(status_code=404, detail="해당 세션을 찾을 수 없습니다")

@app.get("/")
async def get_index():
    """테스트용 HTML 페이지 (디버깅 패널 포함)"""
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
    <title>식품안전법령 검증 시스템 v2.0 (디버깅 강화)</title>
    <meta charset="UTF-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body { 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1800px;
            margin: 0 auto;
        }
        
        h1 { 
            color: white;
            text-align: center;
            margin-bottom: 30px;
            font-size: 2.2rem;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }
        
        .main-grid {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
            align-items: start;
        }
        
        .card {
            background: white;
            border-radius: 15px;
            padding: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            max-height: 600px;
            display: flex;
            flex-direction: column;
        }
        
        .upload-area {
            border: 3px dashed #667eea;
            border-radius: 10px;
            padding: 30px;
            text-align: center;
            transition: all 0.3s;
            background: #f8f9ff;
        }
        
        .upload-area:hover {
            border-color: #764ba2;
            background: #f0f2ff;
        }
        
        .upload-area h3 {
            color: #667eea;
            margin-bottom: 15px;
            font-size: 1.2rem;
        }
        
        #file-input {
            margin: 10px 0;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 5px;
            width: 100%;
        }
        
        .btn {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 20px;
            cursor: pointer;
            font-size: 14px;
            margin: 3px;
            transition: transform 0.2s;
        }
        
        .btn:hover {
            transform: translateY(-2px);
        }
        
        .btn:disabled {
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }
        
        .btn-small {
            padding: 5px 15px;
            font-size: 12px;
        }
        
        #markdown-preview {
            display: none;
        }
        
        #markdown-preview h3 {
            color: #667eea;
            margin-bottom: 10px;
            font-size: 1.1rem;
        }
        
        #markdown-content {
            background: #f5f5f5;
            padding: 10px;
            border-radius: 5px;
            max-height: 200px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 11px;
            line-height: 1.4;
        }
        
        .scrollable-content {
            flex: 1;
            overflow-y: auto;
            margin-top: 10px;
        }
        
        .section-title {
            color: #667eea;
            margin-bottom: 15px;
            font-size: 1.1rem;
            position: sticky;
            top: 0;
            background: white;
            padding: 10px 0;
            border-bottom: 1px solid #eee;
        }
        
        .agent-message {
            margin: 8px 0;
            padding: 10px;
            border-left: 4px solid #667eea;
            background: #f8f9ff;
            border-radius: 5px;
            animation: slideIn 0.3s;
            font-size: 13px;
        }
        
        .debug-item {
            margin: 8px 0;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 5px;
            background: #f9f9f9;
            font-size: 12px;
        }
        
        .debug-item pre {
            background: #f0f0f0;
            padding: 8px;
            border-radius: 3px;
            overflow-x: auto;
            font-size: 11px;
            max-height: 150px;
            overflow-y: auto;
        }
        
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateX(-20px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }
        
        .agent-name {
            font-weight: bold;
            color: #764ba2;
        }
        
        .timestamp {
            font-size: 11px;
            color: #999;
            float: right;
        }
        
        .status-started { 
            border-left-color: #2ecc71; 
            background: #e8f8f5;
        }
        
        .status-processing { 
            border-left-color: #f39c12; 
            background: #fef5e7;
        }
        
        .status-completed { 
            border-left-color: #27ae60; 
            background: #d5f4e6;
        }
        
        .status-error { 
            border-left-color: #e74c3c; 
            background: #ffebee;
        }
        
        .final-result {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 10px;
            margin-top: 20px;
            grid-column: 1 / -1;
        }
        
        .final-result h3 {
            color: white;
            margin-bottom: 15px;
        }
        
        .final-result-content {
            white-space: pre-wrap;
            line-height: 1.6;
            max-height: 500px;
            overflow-y: auto;
            background: rgba(255,255,255,0.1);
            padding: 15px;
            border-radius: 5px;
        }
        
        .loading {
            display: inline-block;
            width: 16px;
            height: 16px;
            border: 2px solid #f3f3f3;
            border-top: 2px solid #667eea;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-left: 8px;
            vertical-align: middle;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .controls {
            display: flex;
            gap: 10px;
            margin-top: 10px;
            align-items: center;
        }
        
        .controls button {
            font-size: 12px;
            padding: 5px 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔬 식품안전법령 검증 시스템 v2.0 (디버깅 강화)</h1>
        
        <div class="main-grid">
            <!-- 파일 업로드 패널 -->
            <div class="card">
                <div class="upload-area">
                    <h3>📁 파일 업로드</h3>
                    <p>Excel 파일을 업로드하여 원료의 식품안전법령 적합성을 검증합니다</p>
                    <input type="file" id="file-input" accept=".xlsx,.xls,.csv">
                    <br>
                    <button class="btn" onclick="uploadFile()">파일 업로드</button>
                    <button class="btn" onclick="startVerification()" id="verify-btn" disabled>검증 시작</button>
                </div>
                
                <div id="markdown-preview">
                    <h3>📄 변환된 Markdown 데이터</h3>
                    <pre id="markdown-content"></pre>
                </div>
            </div>
            
            <!-- 에이전트 작업 현황 패널 -->
            <div class="card">
                <div class="section-title">
                    🤖 에이전트 작업 현황 <span id="loading-spinner"></span>
                </div>
                <div class="scrollable-content">
                    <div id="messages"></div>
                </div>
            </div>
            
            <!-- 디버깅 정보 패널 -->
            <div class="card">
                <div class="section-title">
                    🔍 디버깅 정보
                    <div class="controls">
                        <button class="btn btn-small" onclick="downloadLog()">로그 다운로드</button>
                        <button class="btn btn-small" onclick="clearDebugPanel()">초기화</button>
                    </div>
                </div>
                <div class="scrollable-content">
                    <div id="debug-panel"></div>
                </div>
            </div>
        </div>
        
        <div id="final-result-container"></div>
    </div>
    
    <script>
        let ws = null;
        let sessionId = 'session_' + Date.now();
        let markdownContent = '';
        
        // WebSocket 연결
        function connectWebSocket() {
            ws = new WebSocket(`ws://localhost:8000/ws/${sessionId}`);
            
            ws.onopen = () => {
                console.log('WebSocket 연결됨');
                addMessage('System', 'WebSocket 연결 성공 - 디버깅 모드 활성화', 'started');
            };
            
            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                
                if (data.type === 'agent_status') {
                    addMessage(data.agent, data.message, data.status);
                    
                    // 작업 중 표시
                    if (data.status === 'processing') {
                        document.getElementById('loading-spinner').innerHTML = '<span class="loading"></span>';
                    } else if (data.status === 'completed') {
                        document.getElementById('loading-spinner').innerHTML = '';
                    }
                } else if (data.type === 'debug_info') {
                    addDebugInfo(data.debug_type, data.data);
                } else if (data.type === 'final_result') {
                    displayFinalResult(data.content);
                    document.getElementById('loading-spinner').innerHTML = '';
                }
            };
            
            ws.onclose = () => {
                console.log('WebSocket 연결 끊김');
                addMessage('System', 'WebSocket 연결 끊김', 'error');
                document.getElementById('loading-spinner').innerHTML = '';
            };
        }
        
        // 메시지 추가
        function addMessage(agent, message, status) {
            const messagesDiv = document.getElementById('messages');
            const messageDiv = document.createElement('div');
            messageDiv.className = `agent-message status-${status}`;
            
            const time = new Date().toLocaleTimeString();
            
            // 메시지가 너무 길면 줄임
            const displayMessage = message.length > 300 
                ? message.substring(0, 300) + '...' 
                : message;
            
            messageDiv.innerHTML = `
                <span class="timestamp">${time}</span>
                <span class="agent-name">${agent}:</span> ${displayMessage}
            `;
            
            messagesDiv.appendChild(messageDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }
        
        // 디버깅 정보 표시
        function addDebugInfo(debugType, data) {
            const debugPanel = document.getElementById('debug-panel');
            const debugDiv = document.createElement('div');
            debugDiv.className = 'debug-item';
            
            const time = new Date().toLocaleTimeString();
            
            debugDiv.innerHTML = `
                <strong>[${time}] ${debugType}:</strong>
                <pre>${JSON.stringify(data, null, 2)}</pre>
            `;
            
            debugPanel.appendChild(debugDiv);
            debugPanel.scrollTop = debugPanel.scrollHeight;
        }
        
        // 최종 결과 표시
        function displayFinalResult(content) {
            const container = document.getElementById('final-result-container');
            container.innerHTML = `
                <div class="final-result">
                    <h3>📋 최종 검증 결과</h3>
                    <div class="final-result-content">${content}</div>
                </div>
            `;
        }
        
        // 파일 업로드
        async function uploadFile() {
            const fileInput = document.getElementById('file-input');
            const file = fileInput.files[0];
            
            if (!file) {
                alert('파일을 선택해주세요');
                return;
            }
            
            const formData = new FormData();
            formData.append('file', file);
            
            try {
                addMessage('System', `파일 업로드 중: ${file.name}`, 'processing');
                
                const response = await fetch('/upload', {
                    method: 'POST',
                    body: formData
                });
                
                const result = await response.json();
                
                if (result.status === 'success') {
                    markdownContent = result.content;
                    
                    // Markdown 미리보기 표시
                    document.getElementById('markdown-content').textContent = 
                        markdownContent.substring(0, 1500) + 
                        (markdownContent.length > 1500 ? '...' : '');
                    document.getElementById('markdown-preview').style.display = 'block';
                    
                    // 검증 버튼 활성화
                    document.getElementById('verify-btn').disabled = false;
                    
                    addMessage('System', `파일 업로드 완료 (${result.metadata.converted_size_chars} chars, ${result.metadata.conversion_time_ms}ms)`, 'completed');
                } else {
                    throw new Error('업로드 실패');
                }
                
            } catch (error) {
                addMessage('System', `업로드 오류: ${error.message}`, 'error');
            }
        }
        
        // 검증 시작
        function startVerification() {
            if (!markdownContent) {
                alert('먼저 파일을 업로드해주세요');
                return;
            }
            
            if (!ws || ws.readyState !== WebSocket.OPEN) {
                alert('WebSocket 연결이 끊어졌습니다. 페이지를 새로고침해주세요.');
                return;
            }
            
            // 이전 결과 초기화
            document.getElementById('final-result-container').innerHTML = '';
            
            addMessage('System', '디버깅 강화 식품안전법령 검증 시작...', 'started');
            document.getElementById('loading-spinner').innerHTML = '<span class="loading"></span>';
            
            ws.send(JSON.stringify({
                type: 'start_verification',
                markdown_content: markdownContent
            }));
            
            // 버튼 비활성화
            document.getElementById('verify-btn').disabled = true;
        }
        
        // 로그 다운로드
        async function downloadLog() {
            try {
                const response = await fetch(`/download-log/${sessionId}`);
                if (response.ok) {
                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `debug_log_${sessionId}.json`;
                    a.click();
                    window.URL.revokeObjectURL(url);
                } else {
                    alert('로그 파일을 찾을 수 없습니다');
                }
            } catch (error) {
                alert('로그 다운로드 실패: ' + error.message);
            }
        }
        
        // 디버그 패널 초기화
        function clearDebugPanel() {
            document.getElementById('debug-panel').innerHTML = '';
        }
        
        // 페이지 로드 시 WebSocket 연결
        window.onload = () => {
            connectWebSocket();
        };
        
        // 검증 완료 후 버튼 재활성화
        setInterval(() => {
            if (markdownContent && document.getElementById('verify-btn').disabled && 
                document.getElementById('loading-spinner').innerHTML === '') {
                document.getElementById('verify-btn').disabled = false;
            }
        }, 2000);
    </script>
</body>
</html>
    """)

# ===============================
# 메인 실행
# ===============================

if __name__ == "__main__":
    import uvicorn
    
    print("=" * 80)
    print("식품안전법령 검증 시스템 v2.0 (디버깅 강화)")
    print("=" * 80)
    print("📋 시스템 구성:")
    print("  - MongoDB: 원료별 식품안전법령 정보 저장")
    print("  - MarkItDown: Excel → Markdown 변환")
    print("  - AutoGen: MessageFilter 적용 다중 에이전트 워크플로우")
    print("  - WebSocket: 실시간 진행상황 모니터링")
    print()
    print("🔍 디버깅 기능:")
    print("  - 에이전트별 모든 메시지 완전 기록")
    print("  - MongoDB 쿼리 및 결과 상세 로깅")
    print("  - 실시간 워크플로우 추적")
    print("  - 세션별 JSON 로그 파일 생성")
    print("  - 웹 인터페이스 디버깅 패널")
    print("  - 콘솔 상세 로그 출력")
    print("  - 성능 메트릭 측정")
    print()
    print("✨ v2.0 개선사항:")
    print("  - MessageFilterAgent로 에이전트 환각 방지")
    print("  - TaskDistributor가 Markdown 전체를 분석하여 원료명 추출")
    print("  - MongoDB 검색 결과를 raw JSON으로 전달")
    print("  - 명확한 에이전트 간 메시지 라우팅")
    print("  - 종합 디버깅 시스템 (로깅, 모니터링, 추적)")
    print()
    print("🌐 브라우저에서 http://localhost:8000 접속")
    print("📥 로그 다운로드: http://localhost:8000/download-log/{session_id}")
    print("=" * 80)
    
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)