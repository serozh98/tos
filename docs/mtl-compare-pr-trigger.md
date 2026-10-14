# English/Chinese MTL comparison — chapters 001–054

Goal: compare user-provided Faloo Chinese RAW 001–054 with authorized Wuxiabox English MTL chapters 001–054; analyze name and terminology correspondence, chapter boundaries, numerical claims, and possible MTL errors.

This PR triggers a **single** GitHub Actions collection job using `runs/mtl-1239043-compare.json`, with at least three seconds between all requests, respecting robots.txt, and stops on blocks. It saves its results only in a temporary Actions artifact; no book text is committed to the repository.

The PR should not be merged just to deliver a source archive. The comparison results and agreed glossary are saved to the user's Notion project.
