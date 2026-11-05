module APBGlobalHistoryRegister(
    input logic pclk_level, presetn, pselx, penable, pwrite,
    input logic [9:0] paddr,
    input logic [7:0] pwdata,
    input logic history_shift_valid, clk_gate_en,
    output wire pready,
    output logic [7:0] prdata,
    output wire pslverr, history_full, history_empty, error_flag,
    output wire interrupt_full, interrupt_error
);
    logic gate_enable=1;
    logic [3:0] control_register=0;
    logic [6:0] train_history=0;
    logic [7:0] predict_history=0;
    logic error_latch=0;
    initial prdata=0;
    always_latch begin
        if (!presetn) gate_enable <= 1;
        else if (!pclk_level) gate_enable <= !clk_gate_en;
    end
    wire gated_pclk=pclk_level && gate_enable;
    always_ff @(posedge gated_pclk or negedge presetn) begin
        if (!presetn) begin
            control_register<=0; train_history<=0; prdata<=0; error_latch<=0;
        end else if (pselx && penable) begin
            prdata<=0;
            error_latch <= !(paddr==0 || paddr==1 || (paddr==2 && !pwrite));
            case (paddr)
                10'd0: if(pwrite) control_register<=pwdata[3:0];
                       else prdata<={4'b0,control_register};
                10'd1: if(pwrite) train_history<=pwdata[6:0];
                       else prdata<={1'b0,train_history};
                10'd2: if(!pwrite) prdata<=predict_history;
                default: begin end
            endcase
        end
    end
    always_ff @(posedge history_shift_valid or negedge presetn) begin
        if (!presetn) predict_history<=0;
        else if(control_register[2]) predict_history<={train_history,control_register[3]};
        else if(control_register[0]) predict_history<={predict_history[6:0],control_register[1]};
    end
    assign pready=presetn && gate_enable;
    assign pslverr=error_latch;
    assign error_flag=error_latch;
    assign interrupt_error=error_latch;
    assign history_full=&predict_history;
    assign history_empty=~|predict_history;
    assign interrupt_full=history_full;
endmodule
