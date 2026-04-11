Descição e funcionalidades:

Esse projeto apresenta uma organização de containers focados em realizar comunicação com CLP (Controlador lógico Programavel) e disponibilizar os dados via MQQT Broker para aplicações customizaveis.
O projeto eh divido em duas frentes, 
EDGE-INFRA: lida com a comunicação local com o CLP SIEMENS via ETHERNET, sendo necessario acessar o objeto CLP via o IP do mesmo, tendo em vista que os SIEMENS S7 1200+ tem um servidor OPC UA nativo.
O edge connector disponibiliza as informações captadas via protocolos OPC UA e MQQT, pela arquitetura padrão nesses casos foi usado o protocolo MQTT pra disponiblizar as informações via BROKER, e o OPC UA pra receber informações pertinentes a lógica do CLP.
MQTT publica as informações em tópicos customizaveis no broker.
OPC UA recebe informações atraves do valor e nodeid e insere dentro das funções do CLP.
<img width="531" height="482" alt="image" src="https://github.com/user-attachments/assets/807bab0b-0a08-4e80-a23b-2e0f6e88a722" />
