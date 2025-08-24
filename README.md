# Servidor de Busca de Produtos para TGA

Este projeto implementa uma API RESTful robusta para busca de produtos, simulando um endpoint de ferramentas no estilo MCP. Ele é projetado para ser consumido por um agente de IA ou chatbot, fornecendo uma busca de texto avançada, tolerante a erros de digitação e ordenada por relevância.

O projeto é containerizado com Docker e preparado para deploy em plataformas como o Render.com.

## Funcionalidades

- **Busca de Texto Avançada:** Combina múltiplas estratégias (full-text search, correspondência de todos os termos e similaridade de texto) para máxima relevância.
- **Tolerância a Erros:** Lida bem com erros de digitação e variações de acentuação.
- **Paginação:** Suporta paginação para lidar com grandes volumes de resultados.
- **Segurança:** Acesso à API protegido por chave (API Key).
- **Pronto para Produção:** Utiliza Gunicorn, migrações de banco com Alembic e logs estruturados em JSON.
- **Sincronização Automática:** Sincroniza os dados dos produtos com a API da TGA a cada 6 horas.

## Setup e Instalação

### Pré-requisitos

- Docker e Docker Compose instalados.
- Git.

### 1. Clonar o Repositório

```bash
git clone <url-do-seu-repositorio>
cd api-tga-rei
```

### 2. Configurar Variáveis de Ambiente

Crie um arquivo chamado `.env` na raiz do projeto. Você pode copiar o exemplo:

```bash
cp .env.example .env
```

Agora, edite o arquivo `.env` com suas chaves e segredos:

```env
# Chaves para a API da TGA Sistemas
API_BASE_URL=https://api.tgasistemas.com.br
API_KEY=sua_chave_real_da_api_tga

# Chave de segurança para proteger este servidor
# Use um valor longo e aleatório em produção.
# Ex: gerado com `openssl rand -hex 32`
SERVER_API_KEY=seu_segredo_super_seguro
```

### 3. Rodar o Ambiente com Docker

Suba os containers do back-end e do banco de dados:

```bash
docker-compose up --build
```

A API estará disponível em `http://localhost:8080`.

## Como Usar a API

### Endpoint de Busca

- **URL:** `http://localhost:8080/tool_call`
- **Método:** `POST`
- **Cabeçalhos:**
  - `Content-Type: application/json`
  - `X-API-Key: seu_segredo_super_seguro` (o mesmo valor de `SERVER_API_KEY` no seu `.env`)

- **Corpo da Requisição (Body):**

```json
{
  "tool_name": "search_products",
  "params": {
    "query": "toalha 100% algodão",
    "page": 1
  }
}
```

## Testes

Para rodar a suíte de testes automatizados, execute o seguinte comando:

```bash
docker-compose run --rm backend pytest
```

## Deploy no Render.com

Este projeto pode ser facilmente "deployado" no Render.com usando o arquivo `render.yaml` (que será criado a seguir). As variáveis de ambiente definidas no `.env` deverão ser configuradas na seção "Environment" do serviço no Render.
