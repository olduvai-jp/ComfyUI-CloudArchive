import { app } from "../../scripts/app.js";
import { ComfyButtonGroup } from "../../scripts/ui/components/buttonGroup.js";
import { ComfyButton } from "../../scripts/ui/components/button.js";

const circleSvg = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">
  <circle cx="12" cy="12" r="5" fill="gray" />
</svg>
`

app.registerExtension({ 
	name: "comfyui.cloudarchive.status",
	async setup() { 

    // Create indicator UI
    const buttonGroup = new ComfyButtonGroup()
    const buttonGroupElm = buttonGroup.element

    buttonGroupElm.innerHTML = circleSvg
    buttonGroupElm.style.display = "flex";
    buttonGroupElm.style.alignItems = "center";
    
    // Add tooltip
    buttonGroupElm.title = "Cloud Archive Status: Not Running";

    app.menu?.settingsGroup.element.before(buttonGroupElm);

    // Function to change indicator color
    function changeIndicatorColor(color, status) {
      const circle = buttonGroupElm.querySelector('circle');
      if (circle) {
        circle.setAttribute('fill', color);
      }
      
      // Update tooltip
      if (status) {
        buttonGroupElm.title = `Cloud Archive Status: ${status}`;
      }
    }

    // Initial state is gray (not running)
    changeIndicatorColor('gray', 'Not Running');

    // Function to periodically fetch status
    async function fetchStatus() {
      try {
        const response = await fetch('/cloud-archive/status');
        if (response.ok) {
          const data = await response.json();
          
          // Change indicator color based on status
          if (!data.running) {
            // Inactive state
            changeIndicatorColor('#808080', 'Not Running');
          } else if (data.uploading) {
            // Uploading - yellow for caution
            changeIndicatorColor('#FFC107', 'Uploading - Please do not close');
          } else {
            // Normal monitoring
            changeIndicatorColor('#4CAF50', 'Watching');
          }
        }
      } catch (error) {
        console.error('Failed to fetch cloud archive status:', error);
        changeIndicatorColor('#F44336', 'Error');
      }
    }

    // Initial status fetch
    fetchStatus();
    
    // Fetch status every 5 seconds
    setInterval(fetchStatus, 5000);
	},
})