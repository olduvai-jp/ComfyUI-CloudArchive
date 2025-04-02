import { app } from "../../scripts/app.js";
import { ComfyButtonGroup } from "../../scripts/ui/components/buttonGroup.js";
import { ComfyButton } from "../../scripts/ui/components/button.js";

const circleSvg = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">
  <circle cx="12" cy="12" r="5" fill="gray" />
</svg>
`

app.registerExtension({ 
	name: "comfyui.outputuploader.cloudsync",
	async setup() { 

    // インジケーターUIの作成
    const buttonGroup = new ComfyButtonGroup()
    const buttonGroupElm = buttonGroup.element

    buttonGroupElm.innerHTML = circleSvg
    buttonGroupElm.style.display = "flex";
    buttonGroupElm.style.alignItems = "center";
    
    // ツールチップの追加
    buttonGroupElm.title = "Cloud Sync Status: Not Running";

    app.menu?.settingsGroup.element.before(buttonGroupElm);

    // インジケーターの色を変更する関数
    function changeIndicatorColor(color, status) {
      const circle = buttonGroupElm.querySelector('circle');
      if (circle) {
        circle.setAttribute('fill', color);
      }
      
      // ツールチップの更新
      if (status) {
        buttonGroupElm.title = `Cloud Sync Status: ${status}`;
      }
    }

    // 初期状態は灰色（未実行）
    changeIndicatorColor('gray', 'Not Running');

    // ステータスを定期的に取得する関数
    async function fetchStatus() {
      try {
        const response = await fetch('/cloud-sync/status');
        if (response.ok) {
          const data = await response.json();
          
          // ステータスに応じてインジケーターの色を変更
          if (!data.running) {
            // 非アクティブ状態
            changeIndicatorColor('#808080', 'Not Running');
          } else if (data.uploading) {
            // アップロード中 - 注意喚起のための黄色
            changeIndicatorColor('#FFC107', 'Uploading - Please do not close');
          } else {
            // 正常監視中
            changeIndicatorColor('#4CAF50', 'Watching');
          }
        }
      } catch (error) {
        console.error('Failed to fetch cloud sync status:', error);
        changeIndicatorColor('#F44336', 'Error');
      }
    }

    // 初回ステータス取得
    fetchStatus();
    
    // 5秒ごとにステータスを取得
    setInterval(fetchStatus, 5000);
	},
})