from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import hashlib

from database import SessionLocal, engine, Base
from models import User as UserModel, Message as MessageModel
from schemas import User

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
     allow_origins=[
        "http://localhost:3000",  # development sırasında
        "https://chatnell.com",   # canlı domain
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

active_connections = {}

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/register")
def register(user: User, db: Session = Depends(get_db)):
    existing_user = db.query(UserModel).filter(UserModel.username == user.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Kullanıcı zaten var.")
    hashed_pwd = hash_password(user.password)
    new_user = UserModel(username=user.username, hashed_password=hashed_pwd)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "Kayıt başarılı."}

@app.post("/login")
def login(user: User, db: Session = Depends(get_db)):
    db_user = db.query(UserModel).filter(UserModel.username == user.username).first()
    if not db_user:
        raise HTTPException(status_code=400, detail="Kullanıcı bulunamadı.")
    hashed = hash_password(user.password)
    if hashed != db_user.hashed_password:
        raise HTTPException(status_code=400, detail="Geçersiz şifre.")
    return {"message": "Giriş başarılı."}

@app.get("/users")
def get_users(db: Session = Depends(get_db)):
    users = db.query(UserModel).all()
    return [{"id": user.id, "username": user.username} for user in users]

@app.get("/messages/{user1}/{user2}")
def get_messages(user1: str, user2: str, db: Session = Depends(get_db)):
    messages = (
        db.query(MessageModel)
        .filter(
            ((MessageModel.sender == user1) & (MessageModel.receiver == user2)) |
            ((MessageModel.sender == user2) & (MessageModel.receiver == user1))
        )
        .order_by(MessageModel.id)
        .all()
    )
    return [
        {
            "from_user": msg.sender,
            "to_user": msg.receiver,
            "message": msg.message,
        }
        for msg in messages
    ]

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await websocket.accept()
    active_connections[username] = websocket
    db = SessionLocal()
    try:
        while True:
            data = await websocket.receive_json()
            to_user = data.get("to_user")
            message_text = data.get("message")

            if not to_user or not message_text:
                # Gelen veri eksikse devam etme, hata döndür
                await websocket.send_json({"error": "to_user ve message zorunludur."})
                continue

            # Veritabanına mesajı kaydet
            db_message = MessageModel(sender=username, receiver=to_user, message=message_text)
            db.add(db_message)
            db.commit()
            db.refresh(db_message)

            msg_dict = {
                "from_user": username,
                "to_user": to_user,
                "message": message_text,
            }

            # Eğer alıcı bağlıysa ona mesajı yolla
            if to_user in active_connections:
                try:
                    await active_connections[to_user].send_json(msg_dict)
                except Exception:
                    # Eğer gönderirken hata olursa bağlantıyı kaldır
                    del active_connections[to_user]

            # Mesajı gönderene de gönder
            await websocket.send_json(msg_dict)

    except WebSocketDisconnect:
        if username in active_connections:
            del active_connections[username]
    except Exception as e:
        print(f"WebSocket hata: {e}")
    finally:
        db.close()
        if username in active_connections:
            del active_connections[username]



