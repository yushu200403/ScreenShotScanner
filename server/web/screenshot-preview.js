(() => {
  function openPreview(trigger) {
    const preview = document.createElement("dialog");
    preview.id = "screenshot-preview";
    preview.className = "screenshot-preview";
    preview.setAttribute("aria-label", "截图预览");
    preview.innerHTML = `<div class="screenshot-preview-toolbar"><h3>截图预览</h3><button type="button" class="secondary-button screenshot-size" disabled aria-pressed="false">原始尺寸</button><button type="button" class="secondary-button screenshot-retry hidden">重新加载</button><button type="button" class="quiet-button screenshot-close" aria-label="关闭图片预览" title="关闭"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div><div class="screenshot-stage" tabindex="0" aria-label="截图内容"><p class="screenshot-message" role="status">正在加载截图...</p></div>`;
    const stage = preview.querySelector(".screenshot-stage");
    const message = preview.querySelector(".screenshot-message");
    const sizeButton = preview.querySelector(".screenshot-size");
    const retryButton = preview.querySelector(".screenshot-retry");
    const url = trigger.href;
    function loadImage() {
      stage.querySelector("img")?.remove();
      preview.classList.remove("actual-size");
      sizeButton.disabled = true;
      sizeButton.textContent = "原始尺寸";
      sizeButton.setAttribute("aria-pressed", "false");
      retryButton.classList.add("hidden");
      message.hidden = false;
      message.textContent = "正在加载截图...";
      const picture = new Image();
      picture.alt = "本次问答的电脑截图";
      picture.hidden = true;
      picture.onload = () => {
        if (!preview.open || !picture.isConnected) return;
        picture.hidden = false;
        message.hidden = true;
        sizeButton.disabled = false;
      };
      picture.onerror = () => {
        if (!preview.open || !picture.isConnected) return;
        message.textContent = "图片无法加载，可能已被清理或无权查看。";
        retryButton.classList.remove("hidden");
      };
      stage.append(picture);
      picture.src = url;
    }
    sizeButton.addEventListener("click", () => {
      const actualSize = preview.classList.toggle("actual-size");
      sizeButton.textContent = actualSize ? "适应窗口" : "原始尺寸";
      sizeButton.setAttribute("aria-pressed", String(actualSize));
      stage.scrollTo(0, 0);
    });
    retryButton.addEventListener("click", loadImage);
    preview.querySelector(".screenshot-close").addEventListener("click", () => preview.close());
    preview.addEventListener("click", event => {
      const bounds = preview.getBoundingClientRect();
      if (event.target === preview && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) preview.close();
    });
    preview.addEventListener("close", () => {
      preview.remove();
      const returnTarget = trigger.isConnected ? trigger : Array.from(document.querySelectorAll("[data-screenshot-preview]")).find(link => link.href === url);
      if (returnTarget?.getClientRects().length) returnTarget.focus({preventScroll: true});
    }, {once: true});
    document.body.append(preview);
    preview.showModal();
    loadImage();
  }

  document.addEventListener("click", event => {
    const trigger = event.target.closest("[data-screenshot-preview]");
    if (!trigger || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (!document.querySelector("#screenshot-preview")) openPreview(trigger);
  }, true);
})();
