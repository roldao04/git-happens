export const amount = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 1 });
export const withUnit = (text: string, unit: string) => (unit === "%" ? `${text}%` : `${text} ${unit}`);
