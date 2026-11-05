module apb_dsp_unit(
    input logic pclk_level, presetn, pselx, penable, pwrite,
    input logic [9:0] paddr, input logic [7:0] pwdata,
    input logic sram_valid,
    output wire pready, output logic [7:0] prdata, output logic pslverr
);
    logic [7:0] operand1=0, operand2=0, mode=0, write_address=0, write_data=0;
    logic [7:0] result=0;
    logic [7:0] memory[0:1023];
    initial begin
        prdata=0; pslverr=0;
        for(integer i=0;i<1024;i=i+1) memory[i]=0;
    end
    assign pready=presetn;
    always_ff @(posedge sram_valid)
        if(presetn && mode==3 && write_address>=6) memory[write_address]<=write_data;
    always_ff @(posedge pclk_level or negedge presetn) begin
        if(!presetn) begin
            operand1<=0; operand2<=0; mode<=0; write_address<=0; write_data<=0;
            result<=0; prdata<=0; pslverr<=0;
        end else begin
            if(mode==1) result<=memory[operand1]+memory[operand2];
            else if(mode==2) result<=memory[operand1]*memory[operand2];
            if(pselx && penable) begin
                pslverr<=0;
                if(pwrite) begin
                    prdata<=0;
                    case(paddr)
                        0: operand1<=pwdata;
                        1: operand2<=pwdata;
                        2: if(pwdata<4) mode<=pwdata; else pslverr<=1;
                        3: write_address<=pwdata;
                        4: write_data<=pwdata;
                        default: pslverr<=1;
                    endcase
                end else begin
                    case(paddr)
                        0: prdata<=operand1;
                        1: prdata<=operand2;
                        2: prdata<=mode;
                        3: prdata<=write_address;
                        4: prdata<=write_data;
                        5: prdata<=result;
                        default: prdata<=memory[paddr];
                    endcase
                end
            end
        end
    end
endmodule
