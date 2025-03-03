# app.py
import streamlit as st
import os
import tempfile
from processing_sql import SQLFactoryLoader
from models import init_db, User, Document, Conversation, Message
from datetime import datetime
from langchain.retrievers import MultiQueryRetriever
from langchain_openai.llms import OpenAI
import dotenv

dotenv.load_dotenv()

# Inicializar banco de dados
db_session = init_db()

# Configuração do Streamlit
st.set_page_config(page_title="Chat com Documentos", layout="wide")

# Inicializar estado da sessão
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "username" not in st.session_state:
    st.session_state.username = None
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None

# Funções de autenticação
def login(username, password):
    user = db_session.query(User).filter_by(username=username).first()
    if user and user.check_password(password):
        user.last_login = datetime.utcnow()
        db_session.commit()
        
        st.session_state.user_id = user.user_id
        st.session_state.username = user.username
        return True
    return False

def register(username, email, password):
    # Verificar se usuário ou email já existem
    existing_user = db_session.query(User).filter(
        (User.username == username) | (User.email == email)
    ).first()
    
    if existing_user:
        return False
    
    # Criar novo usuário
    new_user = User(username=username, email=email)
    new_user.set_password(password)
    
    db_session.add(new_user)
    db_session.commit()
    
    return True

def logout():
    st.session_state.user_id = None
    st.session_state.username = None
    st.session_state.conversation_id = None
    st.rerun()

# Função para processar documentos
def process_documents(uploaded_files):
    if not st.session_state.user_id:
        st.error("Você precisa estar logado para processar documentos.")
        return None
    
    factory = SQLFactoryLoader(db_session)
    document_ids = []
    
    for file in uploaded_files:
        # Salvar arquivo no sistema e no banco
        document = factory.save_file(file, st.session_state.user_id)
        document_ids.append(document.document_id)
        
        # Processar documento
        try:
            factory.process_document(document.document_id)
            st.success(f"Documento {file.name} processado com sucesso!")
        except Exception as e:
            st.error(f"Erro ao processar {file.name}: {str(e)}")
    
    return document_ids

# Função para criar uma nova conversa
def create_conversation(title="Nova Conversa"):
    if not st.session_state.user_id:
        return None
    
    conversation = Conversation(
        user_id=st.session_state.user_id,
        title=title
    )
    
    db_session.add(conversation)
    db_session.commit()
    
    return conversation.conversation_id

# Função para salvar mensagem no histórico
def save_message(conversation_id, is_user, content):
    if not conversation_id:
        return
    
    message = Message(
        conversation_id=conversation_id,
        is_user=is_user,
        content=content
    )
    
    db_session.add(message)
    db_session.commit()

# Função para obter histórico de mensagens
def get_conversation_messages(conversation_id):
    if not conversation_id:
        return []
    
    messages = db_session.query(Message).filter_by(
        conversation_id=conversation_id
    ).order_by(Message.timestamp).all()
    
    return [{"role": "user" if msg.is_user else "assistant", "content": msg.content} for msg in messages]

# Função para gerar resposta da IA
def generate_ai_response(query):
    if not st.session_state.user_id:
        return "Você precisa estar logado para usar o chat."
    
    try:
        factory = SQLFactoryLoader(db_session)
        retrievers = factory.get_all_user_retrievers(st.session_state.user_id)
        
        if not retrievers:
            return "Nenhum documento processado encontrado. Por favor, faça upload e processe documentos primeiro."
        
        # Combinar resultados de todos os retrievers
        results = []
        for retriever in retrievers:
            docs = retriever.get_relevant_documents(query)
            results.extend(docs)
        
        # Construir resposta baseada nos resultados
        if not results:
            return "Não encontrei informações relevantes nos documentos. Tente reformular sua pergunta."
        
        resposta = ""
        for doc in results[:5]:  # Limitar a 5 resultados para não sobrecarregar
            resposta += f"{doc.page_content}\n\n"
            
        return resposta.strip()
        
    except Exception as e:
        return f"Erro ao gerar resposta: {str(e)}"

# Autenticação UI
def auth_ui():
    st.sidebar.title("🔐 Autenticação")
    
    if st.session_state.user_id:
        st.sidebar.success(f"Logado como {st.session_state.username}")
        if st.sidebar.button("Logout"):
            logout()
    else:
        tab1, tab2 = st.sidebar.tabs(["Login", "Cadastro"])
        
        with tab1:
            with st.form("login_form"):
                username = st.text_input("Usuário")
                password = st.text_input("Senha", type="password")
                submit = st.form_submit_button("Login")
                
                if submit:
                    if login(username, password):
                        st.success("Login realizado com sucesso!")
                        st.rerun()
                    else:
                        st.error("Usuário ou senha incorretos")
        
        with tab2:
            with st.form("register_form"):
                new_username = st.text_input("Nome de usuário")
                new_email = st.text_input("Email")
                new_password = st.text_input("Senha", type="password")
                submit = st.form_submit_button("Cadastrar")
                
                if submit:
                    if register(new_username, new_email, new_password):
                        st.success("Cadastro realizado com sucesso! Faça login.")
                    else:
                        st.error("Nome de usuário ou email já existem")

# Upload de documentos UI
def document_upload_ui():
    st.sidebar.title("📄 Documentos")
    
    if not st.session_state.user_id:
        st.sidebar.info("Faça login para gerenciar documentos")
        return
    
    # Listar documentos do usuário
    documents = db_session.query(Document).filter_by(user_id=st.session_state.user_id).all()
    
    if documents:
        st.sidebar.subheader("Seus documentos")
        for doc in documents:
            status = "✅ Processado" if doc.processed else "⏳ Pendente"
            st.sidebar.text(f"{doc.document_name} - {status}")
    
    # Upload de novos documentos
    uploaded_files = st.sidebar.file_uploader(
        "Arraste arquivos PDF/TXT",
        type=["pdf", "txt"],
        accept_multiple_files=True
    )
    
    if uploaded_files and st.sidebar.button("Processar Documentos"):
        with st.spinner("Processando documentos..."):
            process_documents(uploaded_files)
            st.rerun()

# Interface de conversas
def conversation_ui():
    st.sidebar.title("💬 Conversas")
    
    if not st.session_state.user_id:
        st.sidebar.info("Faça login para ver conversas")
        return
    
    # Botão para nova conversa
    if st.sidebar.button("Nova Conversa"):
        st.session_state.conversation_id = create_conversation()
        st.rerun()
    
    # Listar conversas existentes
    conversations = db_session.query(Conversation).filter_by(
        user_id=st.session_state.user_id
    ).order_by(Conversation.created_at.desc()).all()
    
    if conversations:
        st.sidebar.subheader("Histórico de Conversas")
        for conv in conversations:
            # Formatação da data para exibição
            date_str = conv.created_at.strftime("%d/%m/%Y")
            title = conv.title or f"Conversa {date_str}"
            
            if st.sidebar.button(title, key=f"conv_{conv.conversation_id}"):
                st.session_state.conversation_id = conv.conversation_id
                st.rerun()

# Interface principal do chat
def chat_ui():
    st.title("📚 Chat Inteligente com Documentos")
    
    if not st.session_state.user_id:
        st.info("Faça login para usar o chat")
        return
    
    # Verificar se existem documentos processados
    documents = db_session.query(Document).filter_by(
        user_id=st.session_state.user_id,
        processed=True
    ).all()
    
    if not documents:
        st.warning("Você ainda não tem documentos processados. Faça upload de documentos primeiro.")
        return
    
    # Criar conversa se não existir
    if not st.session_state.conversation_id:
        st.session_state.conversation_id = create_conversation()
    
    # Carregar histórico de mensagens
    messages = get_conversation_messages(st.session_state.conversation_id)
    
    # Exibir mensagens
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    # Input do usuário
    if prompt := st.chat_input("Faça sua pergunta sobre os documentos"):
        # Exibir mensagem do usuário
        with st.chat_message("user"):
            st.markdown(prompt)
        
        # Salvar mensagem do usuário
        save_message(st.session_state.conversation_id, True, prompt)
        
        # Gerar e exibir resposta
        with st.spinner("Processando..."):
            response = generate_ai_response(prompt)
        
        with st.chat_message("assistant"):
            st.markdown(response)
        
        # Salvar resposta
        save_message(st.session_state.conversation_id, False, response)

# Interface principal
def main():
    # Interface de autenticação (sidebar)
    auth_ui()
    
    # Interface de upload de documentos (sidebar)
    document_upload_ui()
    
    # Interface de conversas (sidebar)
    conversation_ui()
    
    # Interface principal (chat)
    chat_ui()

if __name__ == "__main__":
    main()