module enhanced_fsm_signal_processor (
    input logic i_clk, i_rst_n, i_enable, i_clear, i_ack, i_fault,
    input logic [4:0] i_vector_1, i_vector_2, i_vector_3,
    input logic [4:0] i_vector_4, i_vector_5, i_vector_6,
    output logic o_ready, o_error,
    output logic [1:0] o_fsm_status,
    output logic [7:0] o_vector_1, o_vector_2, o_vector_3, o_vector_4
);
    localparam logic [1:0] IDLE=0, PROCESS=1, READY=2, FAULT=3;
    logic [1:0] state;
    logic [31:0] result_q;
    assign o_fsm_status=state;
    assign o_ready=(state==READY);
    assign o_error=(state==FAULT);
    assign {o_vector_1,o_vector_2,o_vector_3,o_vector_4}=result_q;
    always_ff @(posedge i_clk or negedge i_rst_n) begin
        if (!i_rst_n) begin state<=IDLE; result_q<=0; end
        else if (i_fault) begin state<=FAULT; result_q<=0; end
        else case (state)
            IDLE: if (i_enable) state<=PROCESS;
            PROCESS: begin
                result_q<={i_vector_1,i_vector_2,i_vector_3,i_vector_4,i_vector_5,i_vector_6,2'b11};
                state<=READY;
            end
            READY: if (i_ack) begin state<=IDLE; result_q<=0; end
            FAULT: if (i_clear) begin state<=IDLE; result_q<=0; end
        endcase
    end
endmodule
