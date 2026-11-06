# Microsserviços Didáticos com FastAPI

Este projeto é uma implementação minimalista, prática e didática de uma **Arquitetura de Microsserviços** utilizando **Python** e **FastAPI**. O objetivo principal é demonstrar de forma simples como sistemas distribuídos se estruturam e se comunicam no mundo real.

---

## O Problema que Este Projeto Resolve

Em aplicações tradicionais (**Monolitos**), todas as regras de negócio (usuários, pedidos, pagamentos, produtos) vivem dentro de um único grande sistema e compartilham o mesmo banco de dados. Conforme o sistema cresce, isso traz grandes dores de cabeça:
- **Acoplamento alto:** Mudar uma funcionalidade pode quebrar outra sem aviso.
- **Escalabilidade difícil:** Se apenas o módulo de pedidos estiver sobrecarregado, você é obrigado a escalar o sistema inteiro.
- **Impacto em falhas:** Se um erro corromper o banco ou travar a aplicação, todo o sistema cai junto.

### A Solução Implementada (Microsserviços)
Este projeto resolve esses problemas dividindo a aplicação em **serviços independentes e especializados**:
1. **Isolamento de Domínios:** Cada microsserviço cuida exclusivamente de uma responsabilidade (Usuários ou Pedidos).
2. **Database per Service (Banco por Serviço):** Cada microsserviço possui o seu próprio banco de dados SQLite (`users.db` e `orders.db`). Nenhum serviço acessa diretamente o banco do outro.
3. **Comunicação Controlada (API Gateway):** O cliente externo não sabe que existem vários serviços rodando; ele fala apenas com um ponto de entrada único (**API Gateway**).
4. **Colaboração via HTTP:** Quando o Serviço de Pedidos precisa validar se um usuário existe antes de salvar uma compra, ele realiza uma requisição HTTP síncrona controlada para o Serviço de Usuários.

---

##  Estrutura de Diretórios

```text
microservicos-fastapi/
│
├── api_gateway.py      # Porta 8000 (Roteador/Ponto de entrada)
├── user_service.py     # Porta 8001 (Microsserviço de Usuários)
├── order_service.py    # Porta 8002 (Microsserviço de Pedidos)
└── requirements.txt    # Dependências do projeto