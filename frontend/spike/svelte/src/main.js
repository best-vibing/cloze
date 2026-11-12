import { mount } from "svelte";
import App from "./App.svelte";
// 두 후보의 화면을 똑같이 맞춰 비교하려고 vanilla 후보의 스타일을 그대로 쓴다.
import "../../vanilla/style.css";

mount(App, { target: document.getElementById("app") });
