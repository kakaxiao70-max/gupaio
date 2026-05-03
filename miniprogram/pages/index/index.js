const API_BASE_URL = 'https://chunqiao.xo.je';

function parseScene(scene) {
  if (!scene) return '';
  const decoded = decodeURIComponent(scene);
  const pairs = decoded.split('&');
  for (const pair of pairs) {
    const [key, value] = pair.split('=');
    if (key === 'code' && value) {
      return value;
    }
  }
  return decoded;
}

Page({
  data: {
    stockCode: '688256',
    loading: false,
    error: '',
    analysis: ''
  },

  onLoad(options) {
    const sceneCode = parseScene(options.scene);
    if (sceneCode) {
      this.setData({ stockCode: sceneCode });
    }
  },

  onInput(event) {
    this.setData({
      stockCode: event.detail.value,
      error: ''
    });
  },

  analyze() {
    const stockCode = this.data.stockCode.trim();
    if (!/^\d{6}$/.test(stockCode)) {
      this.setData({ error: '请输入 6 位股票代码' });
      return;
    }

    this.setData({
      loading: true,
      error: '',
      analysis: ''
    });

    const requestUrl = `${API_BASE_URL}/api/analyze`;
    console.log('[wx.request] sending POST to:', requestUrl, 'stockCode:', stockCode);
    wx.request({
      url: requestUrl,
      method: 'POST',
      data: {
        stock_code: stockCode,
        provider: 'deepseek'
      },
      header: {
        'content-type': 'application/json'
      },
      timeout: 120000,
      success: (res) => {
        console.log('[wx.request success] statusCode:', res.statusCode);
        console.log('[wx.request success] response:', JSON.stringify(res.data));
        const data = res.data || {};
        if (res.statusCode !== 200 || !data.ok) {
          console.log('[wx.request] 业务错误, data.ok:', data.ok, 'data.error:', data.error);
          this.setData({ error: data.error || '分析失败，请稍后再试' });
          return;
        }
        this.setData({ analysis: data.analysis || '' });
      },
      fail: (err) => {
        console.log('[wx.request fail] url:', `${API_BASE_URL}/api/analyze`);
        console.log('[wx.request fail] stockCode:', stockCode);
        console.log('[wx.request fail] errMsg:', err.errMsg);
        console.log('[wx.request fail] full error:', JSON.stringify(err));
        this.setData({ error: err.errMsg || '网络请求失败' });
      },
      complete: () => {
        this.setData({ loading: false });
      }
    });
  }
});
