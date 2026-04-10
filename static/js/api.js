/**
 * API 工具模块：对 fetch 的轻量封装
 * 提供 API.get / API.post / API.put / API.delete 方法
 * 所有方法返回 Promise<any>，非 2xx 状态会 throw Error
 */
const API = (() => {
  async function request(method, url, body) {
    const opts = {
      method,
      headers: {},
    };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    let data;
    const ct = res.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      data = await res.json();
    } else {
      data = await res.text();
    }
    if (!res.ok) {
      const msg = (typeof data === 'object' && data.error) ? data.error : `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return data;
  }

  return {
    get:    (url)       => request('GET',    url),
    post:   (url, body) => request('POST',   url, body),
    put:    (url, body) => request('PUT',    url, body),
    delete: (url)       => request('DELETE', url),
  };
})();
