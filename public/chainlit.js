(() => {
    const INFO_DIALOG_CLASS = "northwell-info-dialog";

    function roots() {
        const values = [document];
        const chainlitRoot = window.cl_shadowRootElement;
        if (
            chainlitRoot &&
            chainlitRoot !== document &&
            typeof chainlitRoot.querySelectorAll === "function"
        ) {
            values.push(chainlitRoot);
        }
        return values;
    }

    function renameReadmeAndStyleDialog() {
        for (const root of roots()) {
            const button = root.querySelector("#readme-button");
            if (button) {
                if (button.textContent.trim().toLowerCase() === "readme") {
                    button.textContent = "Info";
                }
                button.setAttribute("aria-label", "Info");
            }

            for (const dialog of root.querySelectorAll('[role="dialog"]')) {
                const title = dialog.querySelector(
                    '[data-radix-dialog-title], [role="heading"], h1, h2, h3'
                );
                const titleText = title?.textContent.trim().toLowerCase();
                const isReadmeDialog =
                    titleText === "readme" ||
                    titleText === "info" ||
                    (dialog.classList.contains("h-screen") &&
                        dialog.classList.contains("w-screen"));

                if (!isReadmeDialog) {
                    continue;
                }

                dialog.classList.add(INFO_DIALOG_CLASS);
                // Chainlit applies its `!rounded-none` utility to this dialog.
                // An important inline value reliably overrides that utility.
                dialog.style.setProperty("border-radius", "24px", "important");
                dialog.style.setProperty("overflow-x", "hidden", "important");
                if (title && titleText === "readme") {
                    title.textContent = "Info";
                }
            }
        }
    }

    const observer = new MutationObserver(renameReadmeAndStyleDialog);

    function start() {
        renameReadmeAndStyleDialog();
        observer.observe(document.documentElement, {
            childList: true,
            subtree: true,
            characterData: true,
        });
        document.addEventListener(
            "click",
            (event) => {
                if (event.target.closest?.("#readme-button")) {
                    window.requestAnimationFrame(renameReadmeAndStyleDialog);
                }
            },
            true
        );
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
})();
