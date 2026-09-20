import katex from '../vendor/katex.mjs';

export const mathOptions = {throwOnError: false, trust: false, maxExpand: 1000, maxSize: 20};

export function renderMath(root) {
  root.querySelectorAll('[data-type="block-math"], [data-type="inline-math"]').forEach(element => {
    if (element.querySelector('.katex, .katex-error')) return;
    const latex = element.textContent;
    try {
      katex.render(latex, element, {
        ...mathOptions, displayMode: element.dataset.type === 'block-math',
      });
    } catch {
      // Resource limits or deeply nested input can throw even with throwOnError off.
      element.textContent = latex;
      element.classList.add('math-error');
      element.title = 'This equation could not be rendered. Edit its LaTeX to correct it.';
    }
  });
}

document.querySelectorAll('.article-body').forEach(renderMath);
