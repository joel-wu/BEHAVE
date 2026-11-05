module axis_joiner (
    input logic clk, rst,
    input logic [7:0] s_axis_tdata_1,
    input logic s_axis_tvalid_1, s_axis_tlast_1,
    input logic [7:0] s_axis_tdata_2,
    input logic s_axis_tvalid_2, s_axis_tlast_2,
    input logic [7:0] s_axis_tdata_3,
    input logic s_axis_tvalid_3, s_axis_tlast_3,
    input logic m_axis_tready,
    output logic s_axis_tready_1, s_axis_tready_2, s_axis_tready_3,
    output logic [7:0] m_axis_tdata,
    output logic m_axis_tvalid, m_axis_tlast,
    output logic [1:0] m_axis_tuser,
    output logic busy
);
    logic [1:0] owner, selected, tag_q;
    logic pending, last_q, room, push, selected_valid, selected_last;
    logic [7:0] data_q, selected_data;
    always_comb begin
        selected=owner;
        if(owner==0) begin
            if(s_axis_tvalid_1) selected=1;
            else if(s_axis_tvalid_2) selected=2;
            else if(s_axis_tvalid_3) selected=3;
        end
        selected_data=0;selected_valid=0;selected_last=0;
        case(selected)
            1: begin selected_data=s_axis_tdata_1;selected_valid=s_axis_tvalid_1;selected_last=s_axis_tlast_1;end
            2: begin selected_data=s_axis_tdata_2;selected_valid=s_axis_tvalid_2;selected_last=s_axis_tlast_2;end
            3: begin selected_data=s_axis_tdata_3;selected_valid=s_axis_tvalid_3;selected_last=s_axis_tlast_3;end
            default: begin end
        endcase
    end
    assign room=(!pending || m_axis_tready) && !(pending && last_q);
    assign s_axis_tready_1=!rst && room && selected==1;
    assign s_axis_tready_2=!rst && room && selected==2;
    assign s_axis_tready_3=!rst && room && selected==3;
    assign push=room && selected_valid;
    assign m_axis_tvalid=pending;
    assign m_axis_tdata=pending ? data_q : 8'b0;
    assign m_axis_tlast=pending && last_q;
    assign m_axis_tuser=pending ? tag_q : 2'b0;
    assign busy=owner!=0;
    always_ff @(posedge clk or posedge rst) begin
        if(rst) begin owner<=0;pending<=0;data_q<=0;last_q<=0;tag_q<=0;end
        else begin
            if(pending && m_axis_tready) begin
                if(last_q) owner<=0;
                pending<=0;
            end
            if(push) begin
                owner<=selected;pending<=1;data_q<=selected_data;last_q<=selected_last;tag_q<=selected;
            end
        end
    end
endmodule
