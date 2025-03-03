# processing_sql.py
from abc import ABC, abstractmethod
import os
import uuid
import shutil
import dotenv
from langchain_community.document_loaders.pdf import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores.chroma import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_openai.llms import OpenAI
from langchain.retrievers.self_query.base import SelfQueryRetriever
from langchain.chains.query_constructor.schema import AttributeInfo
from langchain_community.document_loaders import TextLoader
from sqlalchemy.orm import Session
from models import Document, User

dotenv.load_dotenv()


class SQLFileLoader(ABC):
    def __init__(self, file_path, user_id, document_id, db_session):
        self.file_path = file_path
        self.user_id = user_id
        self.document_id = document_id
        self.db_session = db_session
        self.vector_store_dir = f"vector_stores/user_{user_id}/doc_{document_id}"
        
        # Criar diretório se não existir
        os.makedirs(self.vector_store_dir, exist_ok=True)

    @abstractmethod
    def process_file(self):
        pass
    
    @abstractmethod
    def splitting_text(self):
        pass
    
    @abstractmethod
    def embedding_vector_store(self):
        pass

    @abstractmethod
    def call_ai(self):
        pass
    
    def update_document_status(self, vector_store_id=None):
        document = self.db_session.query(Document).filter_by(document_id=self.document_id).first()
        if document:
            document.processed = True
            if vector_store_id:
                document.vector_store_id = vector_store_id
            self.db_session.commit()


class SQLPdfLoader(SQLFileLoader):
    def process_file(self):
        loader = PyPDFLoader(self.file_path)
        arquivo = loader.load()
        return arquivo
    
    def splitting_text(self):
        split = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            separators=["\n\n", "\n", "", ".", " "]
        )
        docs = split.split_documents(self.process_file())
        return docs
    
    def embedding_vector_store(self):
        embedding = HuggingFaceEmbeddings()
        vector_db = Chroma.from_documents(
            documents=self.splitting_text(),
            embedding=embedding,
            persist_directory=self.vector_store_dir
        )
        
        # Atualizar status do documento no banco de dados
        self.update_document_status(vector_store_id=self.vector_store_dir)
        
        return vector_db
    
    def call_ai(self):
        document_description = "apostilas de informações"
        metadata_info = [
            AttributeInfo(
                name='source',
                description='Nome da apostila de onde o texto original foi retirado.',
                type='string'
            ),
            AttributeInfo(
                name='page',
                description='A página da apostila de onde o texto foi extraído. Número da página.',
                type='integer'
            )
        ]
        
        llm = OpenAI(api_key=os.getenv("openaiKey"))
        retriever = SelfQueryRetriever.from_llm(
            llm,
            self.embedding_vector_store(),
            document_description,
            metadata_info,
            verbose=True
        )
        
        return retriever


class SQLTxtLoader(SQLFileLoader):
    def process_file(self):
        loader = TextLoader(self.file_path)
        arquivo = loader.load()
        return arquivo
    
    def splitting_text(self):
        split = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            separators=["\n\n", "\n", "", ".", " "]
        )
        docs = split.split_documents(self.process_file())
        return docs
    
    def embedding_vector_store(self):
        embedding = HuggingFaceEmbeddings()
        vector_db = Chroma.from_documents(
            documents=self.splitting_text(),
            embedding=embedding,
            persist_directory=self.vector_store_dir
        )
        
        # Atualizar status do documento no banco de dados
        self.update_document_status(vector_store_id=self.vector_store_dir)
        
        return vector_db
    
    def call_ai(self):
        document_description = "apostilas de informações"
        metadata_info = [
            AttributeInfo(
                name='source',
                description='Nome do arquivo de onde o texto original foi retirado.',
                type="string"
            )
        ]
        
        llm = OpenAI(api_key=os.getenv("openaiKey"))
        retriever = SelfQueryRetriever.from_llm(
            llm,
            self.embedding_vector_store(),
            document_description,
            metadata_info,
            verbose=True
        )
        
        return retriever


class SQLFactoryLoader:
    def __init__(self, db_session):
        self.db_session = db_session
    
    def save_file(self, uploaded_file, user_id):
        """Salva o arquivo no sistema e registra no banco de dados"""
        # Gerar nome único para o arquivo
        file_uuid = str(uuid.uuid4())
        file_extension = uploaded_file.type.split('/')[1] if '/' in uploaded_file.type else 'txt'
        
        # Criar diretório para usuário se não existir
        user_dir = f"uploads/user_{user_id}"
        os.makedirs(user_dir, exist_ok=True)
        
        # Caminho do arquivo
        file_path = f"{user_dir}/{file_uuid}.{file_extension}"
        
        # Salvar arquivo
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        
        # Registrar no banco de dados
        document = Document(
            user_id=user_id,
            document_name=uploaded_file.name,
            document_type=uploaded_file.type,
            file_path=file_path,
            processed=False
        )
        
        self.db_session.add(document)
        self.db_session.commit()
        
        return document
    
    def process_document(self, document_id):
        """Processa um documento após o upload"""
        document = self.db_session.query(Document).filter_by(document_id=document_id).first()
        
        if not document:
            raise ValueError(f"Documento {document_id} não encontrado")
        
        if "pdf" in document.document_type.lower():
            loader = SQLPdfLoader(document.file_path, document.user_id, document.document_id, self.db_session)
        else:
            loader = SQLTxtLoader(document.file_path, document.user_id, document.document_id, self.db_session)
        
        # Processa o documento e retorna o retriever
        return loader.call_ai()
    
    def get_document_retriever(self, document_id):
        """Recupera um retriever para um documento já processado"""
        document = self.db_session.query(Document).filter_by(document_id=document_id).first()
        
        if not document or not document.processed or not document.vector_store_id:
            raise ValueError(f"Documento {document_id} não processado ou não encontrado")
        
        embedding = HuggingFaceEmbeddings()
        vector_db = Chroma(persist_directory=document.vector_store_id, embedding_function=embedding)
        
        document_description = "apostilas de informações"
        metadata_info = [
            AttributeInfo(name='source', description='Nome do arquivo', type="string")
        ]
        
        if "pdf" in document.document_type.lower():
            metadata_info.append(
                AttributeInfo(name='page', description='Página do documento', type='integer')
            )
        
        llm = OpenAI(api_key=os.getenv("openaiKey"))
        retriever = SelfQueryRetriever.from_llm(
            llm,
            vector_db,
            document_description,
            metadata_info,
            verbose=True
        )
        
        return retriever
    
    def get_all_user_retrievers(self, user_id):
        """Recupera retrievers para todos os documentos processados de um usuário"""
        documents = self.db_session.query(Document).filter_by(
            user_id=user_id, processed=True
        ).all()
        
        retrievers = []
        for document in documents:
            try:
                retriever = self.get_document_retriever(document.document_id)
                retrievers.append(retriever)
            except Exception as e:
                print(f"Erro ao carregar retriever para documento {document.document_id}: {e}")
        
        return retrievers