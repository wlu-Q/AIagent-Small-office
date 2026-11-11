"""Bounded Qwen HTTP clients. Never include credentials/provider bodies in errors."""
from __future__ import annotations

import math
import httpx


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None, params: dict | None = None):
        super().__init__(message)
        self.public_code = code
        self.public_params = params or {}


class QwenProvider:
    def __init__(self, config: dict):
        self.config = config

    def _post(self, kind, body):
        key = self.config.get(f'{kind}_api_key')
        if not key:
            label = '向量' if kind == 'embedding' else '精排'
            raise ProviderError(f'请先配置{label}模型 API Key', code=f'errors.{kind}_key_required')
        try:
            with httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client:
                response = client.post(self.config[f'{kind}_url'], headers={'Authorization': f'Bearer {key}'}, json=body)
                if response.status_code != 200:
                    reasons = {401: '密钥无效', 403: '模型未授权或账户不可用', 429: '请求限流或额度不足'}
                    raise ProviderError(f'模型接口失败：{reasons.get(response.status_code, "HTTP " + str(response.status_code))}', code={401: 'errors.provider_auth', 403: 'errors.provider_auth', 429: 'errors.provider_rate_limit'}.get(response.status_code, 'errors.provider_http'), params={'status': response.status_code})
                try:
                    result = response.json()
                except ValueError:
                    raise ProviderError('模型接口未返回有效 JSON，请检查接口地址', code='errors.model_api_did_not_return_valid_json_check_the_endpoint_url') from None
                if not isinstance(result, dict):
                    raise ProviderError('模型接口返回格式不正确', code='errors.invalid_model_api_response_format')
                return result
        except httpx.HTTPError as exc:
            raise ProviderError('模型接口连接失败或超时，请检查地址和网络', code='errors.model_api_connection_failed_or_timed_out_check_the_url_and_network') from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for start in range(0, len(texts), 10):
            batch = texts[start:start + 10]
            data = self._post('embedding', {
                'model': self.config['embedding_model'], 'input': batch, 'encoding_format': 'float',
            })
            rows = data.get('data')
            if not isinstance(rows, list) or not all(isinstance(row, dict) and type(row.get('index')) is int for row in rows):
                raise ProviderError('向量接口返回格式不正确', code='errors.invalid_response_format_from_embedding_api')
            rows = sorted(rows, key=lambda row: row['index'])
            if [row.get('index') for row in rows] != list(range(len(batch))):
                raise ProviderError('向量接口返回的文本索引不完整', code='errors.embedding_response_has_incomplete_text_indices')
            for row in rows:
                vector = row.get('embedding')
                if not isinstance(vector, list) or not vector or len(vector) > 8192 or any(
                    type(x) not in (int, float) or not math.isfinite(x) for x in vector
                ):
                    raise ProviderError('向量接口返回无效向量', code='errors.embedding_api_returned_an_invalid_vector')
                if result and len(vector) != len(result[0]):
                    raise ProviderError('向量维度不一致', code='errors.inconsistent_embedding_dimensions')
                result.append(vector)
        return result

    def rerank(self, query: str, texts: list[str]) -> list[int]:
        data = self._post('rerank', {
            'model': self.config['rerank_model'], 'input': {'query': query, 'documents': texts},
            'parameters': {'top_n': min(8, len(texts)), 'return_documents': False},
        })
        output = data.get('output')
        rows = output.get('results') if isinstance(output, dict) else None
        if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
            raise ProviderError('精排接口返回格式不正确', code='errors.invalid_response_format_from_reranking_api')
        indices = [row.get('index') for row in rows]
        if not indices or any(type(i) is not int or not 0 <= i < len(texts) for i in indices) or len(set(indices)) != len(indices):
            raise ProviderError('精排接口返回无效结果', code='errors.invalid_reranking_results')
        scores = [row.get('relevance_score') for row in rows]
        if any(type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1 for score in scores):
            raise ProviderError('精排接口返回无效相关性分数', code='errors.invalid_reranking_relevance_scores')
        # Initial Qwen acceptance floor; a ranking alone does not establish relevance.
        # Treat this as retrieval filtering, never as factual confidence.
        return [index for index, score in zip(indices, scores) if score >= 0.5]
