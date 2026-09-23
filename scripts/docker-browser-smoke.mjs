// Optional real-browser checks for docker-smoke.sh; uses frontend dev dependencies.
import { createRequire } from 'node:module'
const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const [baseURL, phase] = process.argv.slice(2)
if (!baseURL || !['online', 'offline'].includes(phase)) throw new Error('Expected URL and online/offline')
const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  await page.goto(baseURL)
  await expect(page.getByRole('img', { name: /^Направленный граф:/ })).toBeVisible()
  if (phase === 'online') {
    await expect(page.getByText('Локальный анализ готов', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: /Почему этот узел в топе/ }).click()
    await page.getByRole('button', { name: /Разобрать вопрос/ }).click()
    await expect(page.getByText('Локальный ответ', { exact: true })).toBeVisible({ timeout: 20000 })
  } else {
    await expect(page.getByText('Нет связи с помощником', { exact: true })).toBeVisible()
    await page.getByRole('combobox', { name: 'Роль клиента', exact: true }).selectOption('transit')
    await expect(page.getByRole('img', { name: /^Направленный граф:/ })).toBeVisible()
    await page.getByRole('button', { name: /Почему этот узел в топе/ }).click()
    await page.getByRole('button', { name: /Разобрать вопрос/ }).click()
    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page.getByRole('img', { name: /^Направленный граф:/ })).toBeVisible()
  }
  console.log(`Browser ${phase}: OK`)
} finally {
  await browser.close()
}
