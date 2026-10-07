# Pagamentos em Moçambique

## Escolha do gateway

Escolha recomendada para este produto: **checkout alojado e adaptador backend de um agregador contratado que confirme suporte a MZN, M-Pesa, e-Mola, mKesh e Visa/Mastercard para a sua entidade em Moçambique**. Não é possível confirmar disponibilidade comercial, taxas ou acesso API apenas com o documento fornecido. Esta implementação não afirma integração direta com nenhuma operadora.

Antes da escolha, obtenha por escrito: cobertura de cada carteira, liquidação em meticais, requisitos KYC, taxas e prazos, sandbox, mecanismo de assinatura/consulta de transação, reembolsos, resolução de duplicados e checkout de cartão sem recolher PAN/CVV na plataforma. Se nenhum agregador cobrir os quatro meios, o adaptador deve encaminhar cada método para o respetivo fornecedor contratado. Não exponha métodos que o fornecedor não suporte em produção; a implementação inicial lista-os para demonstrar o fluxo solicitado.

**Não há um gateway operacional incluído.** O adaptador precisa ser implementado contra a documentação e sandbox do fornecedor efetivamente contratado. O contrato abaixo é interno à CRISAVYA; não corresponde automaticamente à API de qualquer operadora.

## Variáveis necessárias para modo real

- `PAYMENT_MODE=live`.
- `PAYMENT_ADAPTER_URL`: endpoint HTTPS de criação de checkout do seu adaptador.
- `PAYMENT_ADAPTER_TOKEN`: autenticação do adaptador, guardada no alojamento.
- `PAYMENT_CHECKOUT_HOSTS`: hostnames exatos permitidos para redirecionar para pagamento, separados por vírgula, sem protocolo/caminho.
- `PAYMENT_WEBHOOK_SECRET`: segredo independente usado pelo adaptador para assinar callbacks.
- `APP_SECRET_KEY`: chave persistente de sessões; `COOKIE_SECURE=1` num alojamento HTTPS.

Nunca coloque valores reais no Git. A aplicação não recolhe PIN nem números de cartão. Sem configuração válida, o pedido fica pendente e nenhum acesso é libertado.

## Contrato de criação de checkout

A aplicação envia `POST PAYMENT_ADAPTER_URL` com `Authorization: Bearer <token>`, `Content-Type: application/json` e `Idempotency-Key: <order_id>`:

```json
{"order_id":"id-aleatorio", "amount":750, "currency":"MZN", "method":"mpesa", "email":"aluno@example.test", "phone":"+258840000000"}
```

O valor é **inteiro em meticais**, não centavos. O adaptador deve converter unidades quando o fornecedor usar centavos. Métodos: `mpesa`, `emola`, `mkesh`, `card`.

Resposta esperada:

```json
{"reference":"referencia-do-fornecedor", "checkout_url":"https://checkout.fornecedor-contratado.example/sessao"}
```

O adaptador deve persistir a referência e garantir idempotência, sem iniciar duas cobranças para a mesma chave. A aplicação só redireciona para HTTPS e host permitido. O adaptador deve entregar/repetir o callback após a referência ter sido guardada: se chegar antes, recebe 400 e deve tentar mais tarde. A versão inicial não inclui consulta/reconciliação automática de pedidos pendentes; implemente-a antes de operar com dinheiro real.

## Contrato de confirmação

O adaptador **primeiro verifica o pagamento junto do fornecedor**: assinatura nativa, referência, estado final, valor, moeda e conta beneficiária. Só depois envia para `/api/payments/webhook`:

```json
{"event_id":"evento-unico", "order_id":"id-aleatorio", "reference":"referencia-do-fornecedor", "amount":750, "currency":"MZN", "status":"paid"}
```

Headers:

- `X-Payment-Timestamp`: segundos Unix atuais.
- `X-Payment-Signature`: HMAC-SHA256 hexadecimal de `timestamp + "." + corpo_JSON_bruto`, com `PAYMENT_WEBHOOK_SECRET`.

Tolerância temporal: 5 minutos. A assinatura usa os bytes exatos enviados. O pedido deve ser `live`, com referência correspondente e montante/moeda exatos. O callback repetido responde com sucesso sem criar outra matrícula/notificação. Não envie estado `paid` apenas porque o browser regressou ao site. Estados de falha/reembolso precisam de implementação adicional; neste contrato só confirmações finais pagas são aceites.

Exemplo de assinatura no adaptador Python:

```python
import hashlib, hmac, json, time
stamp = str(int(time.time()))
raw = json.dumps(payload, separators=(",", ":")).encode()
signature = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
# Enviar raw sem voltar a serializar, com os dois headers descritos.
```

## Validação para ativação comercial

Teste no sandbox: pagamento pendente não dá acesso; sucesso confirmado liberta o curso; valor incorreto, assinatura inválida e referência errada são rejeitados; repetição não duplica; abandono do checkout não confirma; cobrança expirada/falhada permanece sem acesso; callback atrasado é reconciliado. Valide carteira por carteira e cartão, falhas de rede, reembolsos e uma transação real controlada antes de publicitar vendas.

Os testes incluídos verificam a aplicação e o contrato interno; não verificam operações reais com M-Pesa/e-Mola/mKesh/Visa. SMS/email e campanhas externas também não são enviados/criados por esta versão.
