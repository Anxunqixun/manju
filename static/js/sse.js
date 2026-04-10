/**
 * SSEClient：EventSource 封装，支持自动重连和事件路由
 *
 * 使用方法：
 *   const client = new SSEClient('项目名称', (evt) => {
 *     console.log(evt.event, evt);
 *   });
 *   client.onConnect = () => console.log('connected');
 *   client.onDisconnect = () => console.log('disconnected');
 *   client.connect();
 *   // 断开: client.disconnect();
 */
class SSEClient {
  constructor(projectName, onEvent) {
    this.projectName = projectName;
    this.onEvent = onEvent;
    this.onConnect = null;
    this.onDisconnect = null;
    this._es = null;
    this._reconnectDelay = 2000;
    this._reconnectTimer = null;
    this._stopped = false;
  }

  connect() {
    if (this._stopped) return;
    const url = `/sse/${encodeURIComponent(this.projectName)}`;
    this._es = new EventSource(url);

    this._es.onopen = () => {
      this._reconnectDelay = 2000;
      if (this.onConnect) this.onConnect();
    };

    this._es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.event === 'heartbeat') return;
        if (this.onEvent) this.onEvent(data);
      } catch (err) {
        // 忽略解析失败
      }
    };

    this._es.onerror = () => {
      this._es.close();
      this._es = null;
      if (this.onDisconnect) this.onDisconnect();
      if (!this._stopped) {
        this._reconnectTimer = setTimeout(() => {
          this._reconnectDelay = Math.min(this._reconnectDelay * 1.5, 30000);
          this.connect();
        }, this._reconnectDelay);
      }
    };
  }

  disconnect() {
    this._stopped = true;
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
    if (this._es) {
      this._es.close();
      this._es = null;
    }
    if (this.onDisconnect) this.onDisconnect();
  }
}
